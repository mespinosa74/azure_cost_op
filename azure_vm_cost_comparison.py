from azure.identity import DefaultAzureCredential
import requests, json
from collections import defaultdict
from datetime import datetime, timedelta
import price_sheet


credential = DefaultAzureCredential()
token = credential.get_token("https://management.azure.com/.default")
access_token = token.token
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}


def fetch_all_resources(subscription_id):
    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/virtualMachines?api-version=2025-04-01"


    results = []
    skip_token = None

    while True:
        body = {}
        if skip_token:
            body["$skipToken"] = skip_token
            resp = requests.post(url, headers=headers, json=body)
        else:
            resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()


        results.extend(data.get("value", []))


        skip_token = data.get("$skipToken")
        if not skip_token:
            break
    if not results:
        return [], [], []
    skus = []
    region = []
    formatted_results = []
    for each in results:
        formatted_results.append({
            "id": each["id"],
            "name": each["name"],
            "location": each.get("location", "N/A"),
            "vmSize": each.get("properties", {}).get("hardwareProfile", {}).get("vmSize", "N/A"),
            "osType": each.get("properties", {}).get("storageProfile", {}).get("osDisk", {}).get("osType", "N/A")
        })
        if each.get("location") not in region:
            region.append(each.get("location"))
        if each.get("properties", {}).get("hardwareProfile", {}).get("vmSize", "N/A") not in skus:
            skus.append(each.get("properties", {}).get("hardwareProfile", {}).get("vmSize", "N/A"))
    # with open("all_resources.json", "w") as f:
    #     json.dump(formatted_results, f, indent=4)
    return formatted_results, skus, region


def fetch_cost_by_resource(subscription_id):
    """
    start_date, end_date: 'YYYY-MM-DD' strings
    Assumes roughly 3 months between start and end.
    Returns: dict[resourceId] = {
        'total_cost_3m': float,
        'active_days': int,
        'avg_monthly_cost': float,
        'is_new': bool,
    }
    """
    start_date = (datetime.now() - timedelta(days=90)).isoformat()
    end_date = datetime.now().isoformat()
    url = (f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version=2025-03-01")

    # body = {
    #     "type": "Usage",
    #     "timeframe": "Custom",
    #     "timePeriod": {
    #         "from": start_date,
    #         "to": end_date
    #     },
    #     "dataset": {
    #         "granularity": "Daily",
    #         "aggregation": {
    #             "totalCost": {"name": "Cost", "function": "Sum"}
    #         },
    #         "grouping": [
    #             {"type": "Dimension", "name": "ResourceId"}
    #         ]

    #     }
    # }

    body = {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {
            "from": start_date,
            "to":   end_date
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
                    "values": [ "microsoft.compute/virtualmachines" ]
                }
            }

        }
    }


    stats = defaultdict(lambda: {"total_cost_3m": 0.0, "active_days": 0})

 
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()
    # with open('cost_per_resource.json', 'w') as w:
    #     json.dump(data, w, indent=4)
    props = data.get("properties", {})


    def process_rows(rows):
        for row in rows:
            full_id = row[2]
            rid = full_id.split('/')[-1]
            cost_raw = row[0]


            try:
                cost = float(cost_raw)
            except (TypeError, ValueError):
                cost = 0.0

            if rid is None:
                continue

            stats[rid]["total_cost_3m"] += cost

            if cost > 0:
                stats[rid]["active_days"] += 1

    process_rows(props.get("rows", []))


    next_link = props.get("nextLink")
    while next_link:
        resp = requests.get(next_link, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        props = data.get("properties", {})
        process_rows(props.get("rows", []))
        next_link = props.get("nextLink")


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
    # with open("cost_by_resource.json", "w") as f:
    #     json.dump(results, f, indent=4)
    return results


def join_data(resources, cost_info, pricing_data, subscription):
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
        
        # Flatten pricing data to make it table-friendly
        pricing_by_location = pricing_data.get(r["location"], {})
        pricing_by_sku = pricing_by_location.get(r["vmSize"], {})
        
        # Extract the most relevant pricing: Linux series first, then Windows
        linux_series = None
        windows_series = None
        
        for product_name, sku_data in pricing_by_sku.items():
            if "Windows" not in product_name and linux_series is None:
                linux_series = (product_name, sku_data)
            elif "Windows" in product_name and windows_series is None:
                windows_series = (product_name, sku_data)
        
        # Choose the appropriate series based on OS
        if r["osType"] == "Linux" and linux_series:
            selected_series = linux_series
        elif r["osType"] == "Windows" and windows_series:
            selected_series = windows_series
        else:
            # Fallback to first available
            selected_series = linux_series or windows_series
        
        if selected_series:
            product_name, sku_data = selected_series
            
            # Find standard (non-Spot, non-Low Priority) pricing
            for sku_name, prices in sku_data.items():
                if "Spot" not in sku_name and "Low Priority" not in sku_name:
                    temp_dict["price_payg_hourly"] = prices.get("payg", "N/A")
                    temp_dict["price_payg_monthly"] = prices.get("payg1Month", "N/A")
                    temp_dict["price_payg_yearly"] = prices.get("payg1Year", "N/A")
                    temp_dict["price_1yr_reserved"] = prices.get("1year", "N/A")
                    temp_dict["price_3yr_reserved"] = prices.get("3year", "N/A")
                    break
            
            # Get Spot pricing if available
            for sku_name, prices in sku_data.items():
                if "Spot" in sku_name:
                    temp_dict["price_spot_hourly"] = prices.get("payg", "N/A")
                    temp_dict["price_spot_monthly"] = prices.get("payg1Month", "N/A")
                    break
            
            # Get Low Priority pricing if available
            for sku_name, prices in sku_data.items():
                if "Low Priority" in sku_name:
                    temp_dict["price_low_priority_hourly"] = prices.get("payg", "N/A")
                    temp_dict["price_low_priority_monthly"] = prices.get("payg1Month", "N/A")
                    break
        
        joined.append(temp_dict)
    return joined

def get_pricing_list(regions, skus):
    pricing_list = price_sheet.get_pricing(regions, skus)
    
    return pricing_list




def generate_html_report(data):
    """Generate a standalone HTML report with embedded JSON data"""
    
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
    
    # Embed the JSON data and timestamp
    json_str = json.dumps(data, indent=2)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    html_content = html_template.replace('DATA_PLACEHOLDER', json_str)
    html_content = html_content.replace('TIMESTAMP_PLACEHOLDER', timestamp)
    
    with open('vm_cost_report.html', 'w', encoding='utf-8') as f:
        f.write(html_content)



if __name__ == "__main__":
    input_message = """
    Input Subscription Id\n
    If entering more than one ensure that they are comma separated or the script will fail.\n
    """
    subscriptions_input = input(input_message)
    subscriptions = subscriptions_input.split(',')
    sub_data = {}
    for each in subscriptions:
        
        subscription = each.strip()
        resources, skus, regions = fetch_all_resources(subscription)
        if not resources:
            print(f"No resources found for subscription {subscription}. Skipping.")
            continue
        costs = fetch_cost_by_resource(subscription)
        pricing_list = get_pricing_list(regions, skus)
        joined_data = join_data(resources, costs, pricing_list, subscription)
        sub_data[subscription] = joined_data
        
    with open('Cost_op_data.json', 'w') as f:
        json.dump(sub_data, f, indent=4)

    # Generate standalone HTML report
    generate_html_report(sub_data)

    print("Files generated:")
    print("  - Cost_op_data.json")
    print("  - vm_cost_report.html (standalone - just open in browser!)")
