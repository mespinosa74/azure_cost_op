from azure.identity import DefaultAzureCredential
from azure.core.exceptions import AzureError
import requests
import json
import sys
import re
from collections import defaultdict
from datetime import datetime, timedelta
import price_sheet


def initialize_azure_credentials():
    """Initialize Azure credentials with error handling"""
    try:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://management.azure.com/.default")
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json"
        }
        return headers
    except Exception as e:
        print(f"Error: Failed to authenticate with Azure")
        print(f"Details: {e}")
        print("\nPlease ensure you are logged in via 'az login' or have valid credentials configured")
        sys.exit(1)


def validate_subscription_id(subscription_id):
    """Validate subscription ID format (GUID)"""
    guid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
    return bool(guid_pattern.match(subscription_id))


def fetch_all_resources(subscription_id, headers):
    """Fetch all VMs from a subscription"""
    if not validate_subscription_id(subscription_id):
        print(f"Warning: '{subscription_id}' does not appear to be a valid subscription ID")
        return [], [], []
    
    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/virtualMachines?api-version=2025-04-01"
    results = []
    skip_token = None
    
    print(f"Fetching VMs for subscription {subscription_id}...")

    try:
        while True:
            body = {}
            if skip_token:
                body["$skipToken"] = skip_token
                resp = requests.post(url, headers=headers, json=body, timeout=60)
            else:
                resp = requests.get(url, headers=headers, timeout=60)
            
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("value", []))

            skip_token = data.get("$skipToken")
            if not skip_token:
                break
    except requests.exceptions.Timeout:
        print(f"Error: Request timed out while fetching VMs for subscription {subscription_id}")
        return [], [], []
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            print(f"Error: Access denied to subscription {subscription_id}. Check your permissions.")
        elif e.response.status_code == 404:
            print(f"Error: Subscription {subscription_id} not found")
        else:
            print(f"Error: HTTP {e.response.status_code} while fetching VMs: {e}")
        return [], [], []
    except requests.exceptions.RequestException as e:
        print(f"Error: Network error while fetching VMs: {e}")
        return [], [], []
    
    if not results:
        return [], [], []
    
    skus = []
    regions = []
    formatted_results = []
    
    for vm in results:
        try:
            location = vm.get("location", "N/A")
            vm_size = vm.get("properties", {}).get("hardwareProfile", {}).get("vmSize", "N/A")
            os_type = vm.get("properties", {}).get("storageProfile", {}).get("osDisk", {}).get("osType", "N/A")
            
            formatted_results.append({
                "id": vm.get("id", ""),
                "name": vm.get("name", "Unknown"),
                "location": location,
                "vmSize": vm_size,
                "osType": os_type
            })
            
            if location not in regions and location != "N/A":
                regions.append(location)
            if vm_size not in skus and vm_size != "N/A":
                skus.append(vm_size)
        except (KeyError, TypeError) as e:
            print(f"Warning: Skipping malformed VM data: {e}")
            continue
    
    print(f"Found {len(formatted_results)} VMs")
    return formatted_results, skus, regions


def fetch_cost_by_resource(subscription_id, headers):
    """Fetch cost data for VMs over the last 90 days"""
    start_date = (datetime.now() - timedelta(days=90)).isoformat()
    end_date = datetime.now().isoformat()
    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version=2025-03-01"

    body = {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {
            "from": start_date,
            "to": end_date
        },
        "dataset": {
            "granularity": "Daily",
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"}
            },
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"}
            ],
            "filter": {
                "dimensions": {
                    "name": "ResourceType",
                    "operator": "In",
                    "values": ["microsoft.compute/virtualmachines"]
                }
            }
        }
    }

    stats = defaultdict(lambda: {"total_cost_3m": 0.0, "active_days": 0})
    
    print(f"Fetching cost data for subscription {subscription_id}...")

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        props = data.get("properties", {})
    except requests.exceptions.Timeout:
        print("Warning: Cost data request timed out, continuing with zero costs")
        return {}
    except requests.exceptions.HTTPError as e:
        print(f"Warning: Could not fetch cost data (HTTP {e.response.status_code}), continuing with zero costs")
        return {}
    except requests.exceptions.RequestException as e:
        print(f"Warning: Network error fetching cost data: {e}, continuing with zero costs")
        return {}

    def process_rows(rows):
        """Process cost data rows"""
        for row in rows:
            try:
                if len(row) < 3:
                    continue
                
                full_id = row[2]
                rid = full_id.split('/')[-1].lower()
                cost_raw = row[0]

                try:
                    cost = float(cost_raw)
                except (TypeError, ValueError):
                    cost = 0.0

                stats[rid]["total_cost_3m"] += cost
                if cost > 0:
                    stats[rid]["active_days"] += 1
            except (IndexError, KeyError, TypeError):
                continue

    process_rows(props.get("rows", []))

    next_link = props.get("nextLink")
    page_count = 1
    max_pages = 100
    
    while next_link and page_count < max_pages:
        try:
            resp = requests.get(next_link, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            props = data.get("properties", {})
            process_rows(props.get("rows", []))
            next_link = props.get("nextLink")
            page_count += 1
        except requests.exceptions.RequestException:
            print("Warning: Error fetching additional cost data pages")
            break

    FULL_WINDOW_DAYS = 90
    MONTHS_IN_WINDOW = 3.0

    results = {}
    for rid, s in stats.items():
        total = s["total_cost_3m"]
        active_days = s["active_days"]
        avg_monthly = total / MONTHS_IN_WINDOW
        one_year_est = avg_monthly * 12
        three_year_est = avg_monthly * 36
        is_new = active_days < FULL_WINDOW_DAYS

        results[rid] = {
            "total_cost_3m": total,
            "active_days": active_days,
            "avg_monthly_cost": avg_monthly,
            "one_year_est": one_year_est,
            "three_year_est": three_year_est,
            "is_new": is_new
        }
    
    print(f"Retrieved cost data for {len(results)} resources")
    return results


def fetch_vm_utilization(subscription_id, resources, headers):
    """Fetch CPU utilization metrics for VMs over the last 30 days"""
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=30)
    
    timespan = f"{start_time.isoformat()}Z/{end_time.isoformat()}Z"
    utilization_data = {}
    
    print(f"Fetching CPU utilization for {len(resources)} VMs...")
    
    for idx, vm in enumerate(resources, 1):
        vm_id = vm.get('id', '')
        vm_name = vm.get('name', 'Unknown')
        
        if not vm_id:
            continue
        
        if idx % 10 == 0:
            print(f"  Progress: {idx}/{len(resources)} VMs")
        
        url = f"https://management.azure.com{vm_id}/providers/Microsoft.Insights/metrics"
        params = {
            "api-version": "2023-10-01",
            "metricnames": "Percentage CPU",
            "timespan": timespan,
            "interval": "PT1H",
            "aggregation": "Average,Maximum"
        }
        
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            metrics = data.get("value", [])
            if not metrics:
                utilization_data[vm_name.lower()] = {
                    "avg_cpu": None,
                    "peak_cpu": None,
                    "recommendation": "No metrics available"
                }
                continue
            
            timeseries = metrics[0].get("timeseries", [])
            if not timeseries or not timeseries[0].get("data"):
                utilization_data[vm_name.lower()] = {
                    "avg_cpu": None,
                    "peak_cpu": None,
                    "recommendation": "No data points"
                }
                continue
            
            data_points = timeseries[0]["data"]
            avg_values = [d["average"] for d in data_points if d.get("average") is not None]
            max_values = [d["maximum"] for d in data_points if d.get("maximum") is not None]
            
            if not avg_values:
                utilization_data[vm_name.lower()] = {
                    "avg_cpu": None,
                    "peak_cpu": None,
                    "recommendation": "No data points"
                }
                continue
            
            avg_cpu = sum(avg_values) / len(avg_values)
            peak_cpu = max(max_values) if max_values else avg_cpu
            
            if avg_cpu < 10 and peak_cpu < 30:
                recommendation = "⚠️ Very low utilization"
            elif avg_cpu < 20 and peak_cpu < 50:
                recommendation = "⚠️ Low utilization"
            elif avg_cpu > 70 or peak_cpu > 90:
                recommendation = "⚡ High utilization"
            else:
                recommendation = "✓ Normal"
            
            utilization_data[vm_name.lower()] = {
                "avg_cpu": round(avg_cpu, 1),
                "peak_cpu": round(peak_cpu, 1),
                "recommendation": recommendation
            }
            
        except requests.exceptions.Timeout:
            utilization_data[vm_name.lower()] = {
                "avg_cpu": None,
                "peak_cpu": None,
                "recommendation": "Timeout"
            }
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"  Warning: Rate limited, skipping remaining VMs")
                break
            utilization_data[vm_name.lower()] = {
                "avg_cpu": None,
                "peak_cpu": None,
                "recommendation": "Error"
            }
        except Exception:
            utilization_data[vm_name.lower()] = {
                "avg_cpu": None,
                "peak_cpu": None,
                "recommendation": "Error"
            }
    
    print(f"Retrieved utilization data for {len(utilization_data)} VMs")
    return utilization_data


def join_data(resources, cost_info, pricing_data, utilization_data=None):
    """Combine VM resources with cost, pricing, and utilization data"""
    joined = []
    
    for r in resources:
        c = cost_info.get(r['name'].lower(), {
            "total_cost_3m": 0.0,
            "active_days": 0,
            "avg_monthly_cost": 0.0,
            "one_year_est": 0.0,
            "three_year_est": 0.0,
            "is_new": True
        })
        
        temp_dict = {
            "name": r["name"],
            "region": r["location"],
            "vmSize": r["vmSize"],
            "osType": r["osType"],
            "total_cost_3m": round(c["total_cost_3m"], 2),
            "avg_monthly_cost": round(c["avg_monthly_cost"], 2),
            "one_year_est": round(c["one_year_est"], 2),
            "three_year_est": round(c["three_year_est"], 2),
            "is_new": c["is_new"]
        }
        
        if utilization_data:
            u = utilization_data.get(r['name'].lower(), {
                "avg_cpu": None,
                "peak_cpu": None,
                "recommendation": "N/A"
            })
            temp_dict["avg_cpu"] = u.get("avg_cpu")
            temp_dict["peak_cpu"] = u.get("peak_cpu")
            temp_dict["utilization_status"] = u.get("recommendation", "N/A")
        
        pricing_by_location = pricing_data.get(r["location"], {})
        pricing_by_sku = pricing_by_location.get(r["vmSize"], {})
        
        linux_series = None
        windows_series = None
        
        for product_name, sku_data in pricing_by_sku.items():
            if "Windows" not in product_name and linux_series is None:
                linux_series = (product_name, sku_data)
            elif "Windows" in product_name and windows_series is None:
                windows_series = (product_name, sku_data)
        
        if r["osType"] == "Linux" and linux_series:
            selected_series = linux_series
        elif r["osType"] == "Windows" and windows_series:
            selected_series = windows_series
        else:
            selected_series = linux_series or windows_series
        
        if selected_series:
            product_name, sku_data = selected_series
            
            for sku_name, prices in sku_data.items():
                if "Spot" not in sku_name and "Low Priority" not in sku_name:
                    temp_dict["price_payg_hourly"] = prices.get("payg", "N/A")
                    temp_dict["price_payg_monthly"] = prices.get("payg1Month", "N/A")
                    temp_dict["price_payg_yearly"] = prices.get("payg1Year", "N/A")
                    temp_dict["price_1yr_reserved"] = prices.get("1year", "N/A")
                    temp_dict["price_3yr_reserved"] = prices.get("3year", "N/A")
                    break
            
            for sku_name, prices in sku_data.items():
                if "Spot" in sku_name:
                    temp_dict["price_spot_hourly"] = prices.get("payg", "N/A")
                    temp_dict["price_spot_monthly"] = prices.get("payg1Month", "N/A")
                    break
            
            for sku_name, prices in sku_data.items():
                if "Low Priority" in sku_name:
                    temp_dict["price_low_priority_hourly"] = prices.get("payg", "N/A")
                    temp_dict["price_low_priority_monthly"] = prices.get("payg1Month", "N/A")
                    break
        
        joined.append(temp_dict)
    return joined


def get_pricing_list(regions, skus):
    """Fetch pricing data for specified regions and SKUs"""
    if not regions or not skus:
        print("Warning: No regions or SKUs found, pricing data will be empty")
        return {}
    
    print(f"Fetching pricing for {len(regions)} regions and {len(skus)} VM sizes...")
    pricing_list = price_sheet.get_pricing(regions, skus)
    
    if not pricing_list:
        print("Warning: No pricing data returned")
    else:
        print("Pricing data retrieved successfully")
    
    return pricing_list




def generate_html_report(data):
    """Generate standalone HTML report with embedded JSON data"""
    if not data:
        print("Warning: No data to generate report")
        return
    
    html_template = '''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Azure VM Cost Comparison</title>
  <style>
    body { 
      font-family: 'Segoe UI', Arial, sans-serif; 
      margin: 0;
      padding: 20px;
      background-color: #f5f5f5;
    }
    .container {
      max-width: 95%;
      margin: 0 auto;
      background: white;
      padding: 30px;
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    h1 {
      color: #0078d4;
      margin-bottom: 10px;
      font-size: 28px;
    }
    .subscription-id {
      color: #666;
      font-size: 14px;
      margin-bottom: 20px;
      font-family: monospace;
    }
    .summary {
      background-color: #f0f6ff;
      padding: 15px;
      border-radius: 6px;
      margin-bottom: 20px;
      display: flex;
      gap: 30px;
      flex-wrap: wrap;
    }
    .summary-item {
      flex: 1;
      min-width: 150px;
    }
    .summary-label {
      font-size: 12px;
      color: #666;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .summary-value {
      font-size: 24px;
      font-weight: bold;
      color: #0078d4;
      margin-top: 5px;
    }
    table { 
      border-collapse: collapse; 
      width: 100%;
      margin-top: 20px;
      font-size: 14px;
    }
    th, td { 
      border: 1px solid #e0e0e0; 
      padding: 12px 10px; 
      text-align: left;
    }
    th { 
      background-color: #0078d4;
      color: white;
      font-weight: 600;
      position: sticky;
      top: 0;
      z-index: 10;
      white-space: nowrap;
    }
    th.sortable {
      cursor: pointer;
      user-select: none;
    }
    th.sortable:hover {
      background-color: #005a9e;
    }
    th.sortable::after {
      content: ' ⇅';
      opacity: 0.5;
    }
    tbody tr:nth-child(even) {
      background-color: #f9f9f9;
    }
    tbody tr:hover {
      background-color: #e9f5ff;
    }
    .vm-name {
      font-weight: 600;
      color: #0078d4;
    }
    .cost-cell {
      text-align: right;
      font-family: 'Courier New', monospace;
    }
    .new-badge {
      background-color: #28a745;
      color: white;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: bold;
      margin-left: 8px;
    }
    .os-badge {
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .os-linux {
      background-color: #e8f5e9;
      color: #2e7d32;
    }
    .os-windows {
      background-color: #e3f2fd;
      color: #1565c0;
    }
    .util-warning {
      background-color: #fff3cd;
      color: #856404;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .util-normal {
      background-color: #d4edda;
      color: #155724;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .util-high {
      background-color: #f8d7da;
      color: #721c24;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .savings {
      color: #28a745;
      font-weight: 600;
    }
    .generated-date {
      text-align: right;
      color: #999;
      font-size: 12px;
      margin-top: 20px;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>Azure VM Cost Comparison</h1>
    <div id="content">
      <div style="text-align: center; padding: 40px;">Loading data...</div>
    </div>
  </div>

  <script>
    // Embedded JSON data
    const jsonData = DATA_PLACEHOLDER;

    function formatCurrency(value) {
      if (value === 'N/A' || value === null || value === undefined || value === '') return 'N/A';
      const num = parseFloat(value);
      if (isNaN(num)) return 'N/A';
      return '$' + num.toFixed(2);
    }

    function calculateSavings(yearlyPayg, reserved) {
      if (yearlyPayg === 'N/A' || reserved === 'N/A') return null;
      const payg = parseFloat(yearlyPayg);
      const res = parseFloat(reserved);
      if (isNaN(payg) || isNaN(res) || payg === 0) return null;
      return ((payg - res) / payg * 100).toFixed(0);
    }

    function createSummary(vms) {
      const totalVMs = vms.length;
      const totalCost3M = vms.reduce((sum, vm) => sum + (vm.total_cost_3m || 0), 0);
      const totalMonthly = vms.reduce((sum, vm) => sum + (vm.avg_monthly_cost || 0), 0);
      const newVMs = vms.filter(vm => vm.is_new).length;

      return `
        <div class="summary">
          <div class="summary-item">
            <div class="summary-label">Total VMs</div>
            <div class="summary-value">${totalVMs}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">3-Month Actual Cost</div>
            <div class="summary-value">${formatCurrency(totalCost3M)}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">Avg Monthly Cost</div>
            <div class="summary-value">${formatCurrency(totalMonthly)}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">New VMs (< 90 days)</div>
            <div class="summary-value">${newVMs}</div>
          </div>
        </div>
      `;
    }

    function createTable(vms) {
      let html = `
        <table>
          <thead>
            <tr>
              <th class="sortable" data-sort="name">VM Name</th>
              <th class="sortable" data-sort="region">Region</th>
              <th class="sortable" data-sort="vmSize">Size</th>
              <th class="sortable" data-sort="osType">OS</th>
              <th class="sortable" data-sort="avg_cpu">Avg CPU %</th>
              <th class="sortable" data-sort="peak_cpu">Peak CPU %</th>
              <th class="sortable" data-sort="utilization_status">Utilization</th>
              <th class="sortable" data-sort="total_cost_3m">3-Mo Actual</th>
              <th class="sortable" data-sort="avg_monthly_cost">Avg/Month</th>
              <th class="sortable" data-sort="price_payg_monthly">PAYG/Month</th>
              <th class="sortable" data-sort="price_payg_yearly">PAYG/Year</th>
              <th class="sortable" data-sort="price_1yr_reserved">1-Yr Reserved</th>
              <th class="sortable" data-sort="price_3yr_reserved">3-Yr Reserved</th>
              <th class="sortable" data-sort="price_spot_monthly">Spot/Month</th>
              <th class="sortable" data-sort="price_low_priority_monthly">Low Priority/Month</th>
              <th>Potential Savings</th>
            </tr>
          </thead>
          <tbody>
      `;

      vms.forEach(vm => {
        const savings1yr = calculateSavings(vm.price_payg_yearly, vm.price_1yr_reserved);
        const savings3yr = calculateSavings(vm.price_payg_yearly, vm.price_3yr_reserved);
        
        // Format CPU utilization
        const avgCpu = vm.avg_cpu !== null && vm.avg_cpu !== undefined ? vm.avg_cpu.toFixed(1) + '%' : 'N/A';
        const peakCpu = vm.peak_cpu !== null && vm.peak_cpu !== undefined ? vm.peak_cpu.toFixed(1) + '%' : 'N/A';
        
        // Determine utilization badge class
        let utilClass = 'util-normal';
        if (vm.utilization_status && vm.utilization_status.includes('⚠️')) {
          utilClass = 'util-warning';
        } else if (vm.utilization_status && vm.utilization_status.includes('⚡')) {
          utilClass = 'util-high';
        }
        
        html += `
          <tr>
            <td>
              <span class="vm-name">${vm.name}</span>
              ${vm.is_new ? '<span class="new-badge">NEW</span>' : ''}
            </td>
            <td>${vm.region}</td>
            <td>${vm.vmSize}</td>
            <td>
              <span class="os-badge os-${vm.osType.toLowerCase()}">${vm.osType}</span>
            </td>
            <td>${avgCpu}</td>
            <td>${peakCpu}</td>
            <td>
              ${vm.utilization_status ? `<span class="${utilClass}">${vm.utilization_status}</span>` : 'N/A'}
            </td>
            <td class="cost-cell">${formatCurrency(vm.total_cost_3m)}</td>
            <td class="cost-cell">${formatCurrency(vm.avg_monthly_cost)}</td>
            <td class="cost-cell">${formatCurrency(vm.price_payg_monthly)}</td>
            <td class="cost-cell">${formatCurrency(vm.price_payg_yearly)}</td>
            <td class="cost-cell">${formatCurrency(vm.price_1yr_reserved)}</td>
            <td class="cost-cell">${formatCurrency(vm.price_3yr_reserved)}</td>
            <td class="cost-cell">${formatCurrency(vm.price_spot_monthly)}</td>
            <td class="cost-cell">${formatCurrency(vm.price_low_priority_monthly)}</td>
            <td>
              ${savings1yr ? `<span class="savings">1yr: ${savings1yr}% off</span><br>` : ''}
              ${savings3yr ? `<span class="savings">3yr: ${savings3yr}% off</span>` : ''}
              ${!savings1yr && !savings3yr ? 'N/A' : ''}
            </td>
          </tr>
        `;
      });

      html += `
          </tbody>
        </table>
      `;

      return html;
    }

    let currentSort = { column: null, ascending: true };

    function sortTable(vms, column) {
      const sorted = [...vms];
      
      sorted.sort((a, b) => {
        let valA = a[column];
        let valB = b[column];
        
        if (typeof valA === 'number' && typeof valB === 'number') {
          return currentSort.ascending ? valA - valB : valB - valA;
        }
        
        valA = String(valA || '').toLowerCase();
        valB = String(valB || '').toLowerCase();
        
        if (valA < valB) return currentSort.ascending ? -1 : 1;
        if (valA > valB) return currentSort.ascending ? 1 : -1;
        return 0;
      });
      
      return sorted;
    }

    function displayData(data) {
      const content = document.getElementById('content');
      const subscriptionIds = Object.keys(data);
      
      if (subscriptionIds.length === 0) {
        content.innerHTML = '<div style="color: red;">No data found.</div>';
        return;
      }

      let allHTML = '';
      
      subscriptionIds.forEach(subId => {
        const vms = data[subId];
        
        allHTML += `
          <div class="subscription-section">
            <div class="subscription-id">Subscription: ${subId}</div>
            ${createSummary(vms)}
            ${createTable(vms)}
          </div>
        `;
      });
      
      allHTML += '<div class="generated-date">Generated: TIMESTAMP_PLACEHOLDER</div>';
      content.innerHTML = allHTML;
      
      document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
          const column = th.dataset.sort;
          
          if (currentSort.column === column) {
            currentSort.ascending = !currentSort.ascending;
          } else {
            currentSort.column = column;
            currentSort.ascending = true;
          }
          
          subscriptionIds.forEach(subId => {
            const sortedVMs = sortTable(data[subId], column);
            data[subId] = sortedVMs;
          });
          
          displayData(data);
        });
      });
    }

    window.addEventListener('DOMContentLoaded', () => {
      displayData(jsonData);
    });
  </script>
</body>
</html>'''
    
    try:
        json_str = json.dumps(data, indent=2)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        html_content = html_template.replace('DATA_PLACEHOLDER', json_str)
        html_content = html_content.replace('TIMESTAMP_PLACEHOLDER', timestamp)
        
        with open('vm_cost_report.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print("Generated: vm_cost_report.html")
    except Exception as e:
        print(f"Error generating HTML report: {e}")


def main():
    """Main execution function"""
    print("Azure VM Cost Comparison Tool")
    print("=" * 50)
    
    headers = initialize_azure_credentials()
    
    input_message = "\nEnter Subscription ID(s) (comma-separated for multiple): "
    subscriptions_input = input(input_message).strip()
    
    if not subscriptions_input:
        print("Error: No subscription ID provided")
        sys.exit(1)
    
    subscriptions = [s.strip() for s in subscriptions_input.split(',')]
    sub_data = {}
    successful_subs = 0
    
    print(f"\nProcessing {len(subscriptions)} subscription(s)...\n")
    
    for idx, subscription in enumerate(subscriptions, 1):
        print(f"\n[{idx}/{len(subscriptions)}] Processing subscription: {subscription}")
        print("-" * 50)
        
        try:
            resources, skus, regions = fetch_all_resources(subscription, headers)
            
            if not resources:
                print(f"Skipping subscription {subscription} - no VMs found\n")
                continue
            
            costs = fetch_cost_by_resource(subscription, headers)
            utilization = fetch_vm_utilization(subscription, resources, headers)
            pricing_list = get_pricing_list(regions, skus)
            joined_data = join_data(resources, costs, pricing_list, utilization)
            
            sub_data[subscription] = joined_data
            successful_subs += 1
            print(f"Successfully processed subscription {subscription}")
            
        except Exception as e:
            print(f"Error processing subscription {subscription}: {e}")
            continue
    
    if not sub_data:
        print("\nError: No data collected from any subscription")
        sys.exit(1)
    
    print(f"\n{'=' * 50}")
    print(f"Successfully processed {successful_subs} of {len(subscriptions)} subscription(s)")
    print(f"{'=' * 50}\n")
    
    try:
        generate_html_report(sub_data)
        print("\nSuccess! Open 'vm_cost_report.html' in your browser to view results")
    except Exception as e:
        print(f"Error generating final report: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)
