# Azure VM Cost Comparison Tool

A comprehensive Python-based tool for analyzing Azure Virtual Machine costs, utilization, and pricing options across multiple subscriptions. Generate detailed HTML reports comparing Pay-As-You-Go (PAYG), Reserved Instances, Spot, and Low Priority pricing to identify cost optimization opportunities.

## 🚀 Features

- **Multi-Subscription Support**: Analyze VMs across multiple Azure subscriptions in a single run
- **Comprehensive Cost Analysis**: 
  - 90-day actual cost history from Azure Cost Management API
  - Monthly average and yearly projections
  - Reserved Instance pricing (1-year and 3-year)
  - Spot and Low Priority pricing options
- **CPU Utilization Metrics**: 
  - 30-day average and peak CPU usage
  - Smart recommendations for underutilized or overloaded VMs
- **Windows License Awareness**:
  - Detects Azure Hybrid Benefit (AHB) usage
  - Accurately calculates Windows license costs for Reserved Instances
  - Properly accounts for PAYG license costs
- **Interactive HTML Reports**:
  - Sortable tables by any column
  - Visual badges for OS type, utilization status, and new VMs
  - Cost savings calculations for Reserved Instances
  - Professional, responsive design
- **Production-Ready**:
  - Robust error handling and retry logic
  - Rate limiting protection
  - Detailed progress reporting
  - Comprehensive logging

## 📋 Prerequisites

- **Python 3.7+**
- **Azure CLI** (authenticated with `az login`) or other Azure credential methods
- **Azure Permissions**:
  - `Reader` role on subscriptions to analyze
  - `Cost Management Reader` for cost data
  - `Monitoring Reader` for CPU metrics

## 🔧 Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd azure_cost_op
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Authenticate with Azure**:
   ```bash
   az login
   ```
   The tool also supports other `DefaultAzureCredential` methods (environment variables, managed identity, etc.)

## � Execution Options

### Option 1: Local Machine
Run the tool directly from your local machine:

1. **Authenticate with Azure CLI**:
   ```bash
   az login
   ```
   > **Note**: This tool uses Azure Management APIs via `az login`, not Microsoft Graph. If you typically use `Connect-MgGraph` for other tasks, you still need `az login` for this script.

2. **Run the script**:
   ```bash
   python azure_vm_cost_comparison.py
   ```

3. **View results**: Open the generated `vm_cost_report.html` in your browser

### Option 2: Azure Cloud Shell
Run the tool from Azure Cloud Shell (no authentication needed - already authenticated):

1. **Open Azure Cloud Shell** (Bash or PowerShell) from the Azure Portal

2. **Upload the script files**:
   - Click the upload/download icon in Cloud Shell
   - Upload `azure_vm_cost_comparison.py`, `price_sheet.py`, and `requirements.txt`
   - Or clone your repository: `git clone <repository-url>`

3. **Install dependencies** (if not already installed):
   ```bash
   pip install -r requirements.txt --user
   ```

4. **Run the script**:
   ```bash
   python azure_vm_cost_comparison.py
   ```

5. **Download the HTML report**:
   - Click the upload/download icon in Cloud Shell
   - Select "Download"
   - Enter the filename: `vm_cost_report.html`
   - Open the downloaded file in your local browser

**Cloud Shell Benefits**:
- No local authentication required
- Pre-configured Azure access
- No need to install Python or dependencies locally

## �💻 Usage

Run the script and enter one or more subscription IDs when prompted:

```bash
python azure_vm_cost_comparison.py
```

**Example**:
```
Azure VM Cost Comparison Tool
==================================================

Enter Subscription ID(s) (comma-separated for multiple): 12345678-1234-1234-1234-123456789abc

Processing 1 subscription(s)...

[1/1] Processing subscription: 12345678-1234-1234-1234-123456789abc
--------------------------------------------------
Fetching VMs for subscription 12345678-1234-1234-1234-123456789abc...
Found 25 VMs
Fetching cost data for subscription 12345678-1234-1234-1234-123456789abc...
Retrieved cost data for 25 resources
Fetching CPU utilization for 25 VMs...
  Progress: 10/25 VMs
  Progress: 20/25 VMs
Retrieved utilization data for 25 VMs
Fetching pricing for 3 regions and 8 VM sizes...
Pricing data retrieved successfully
Successfully processed subscription 12345678-1234-1234-1234-123456789abc

==================================================
Successfully processed 1 of 1 subscription(s)
==================================================

Generated: vm_cost_report.html

Success! Open 'vm_cost_report.html' in your browser to view results
```

## 📊 Output

The tool generates three files:

### 1. `vm_cost_report.html` (Primary Output)
An interactive HTML report featuring:
- **Summary Dashboard**: Total VMs, 3-month costs, monthly averages, new VM count
- **Detailed VM Table** with:
  - VM name, region, size, and OS type
  - License status (PAYG vs AHB)
  - CPU utilization (average, peak, recommendation)
  - Actual costs (3-month, monthly average)
  - Pricing comparisons (PAYG, 1-year RI, 3-year RI, Spot, Low Priority)
  - Potential savings percentages
- **Sortable Columns**: Click any header to sort
- **Visual Indicators**: Color-coded badges for status and recommendations

### 2. `output.json`
Raw JSON data for programmatic access or further analysis

### 3. `pricing_data.json`
Cached pricing data from Azure Pricing API

## 🎯 Understanding the Results

### VM Status Indicators
- **NEW Badge**: VM created within the last 90 days
- **OS Badges**: Color-coded Linux (green) and Windows (blue)

### Utilization Recommendations
- **⚠️ Very low utilization**: Avg CPU < 10%, Peak < 30% - Consider downsizing or deallocating
- **⚠️ Low utilization**: Avg CPU < 20%, Peak < 50% - Review sizing
- **✓ Normal**: Healthy utilization levels
- **⚡ High utilization**: Avg CPU > 70% or Peak > 90% - Consider upgrading

### License Costs
- **PAYG License = "Yes"**: VM is paying for Windows license in hourly rate
- **PAYG License = "No"**: VM uses Azure Hybrid Benefit (bring your own license)
- **Reserved Instance Pricing**: For Windows VMs without AHB, license costs are added to RI pricing (compute discount doesn't apply to licenses)

### Potential Savings
Shows percentage saved by switching from PAYG to:
- **1yr Reserved Instance**: Typically 30-40% savings
- **3yr Reserved Instance**: Typically 50-60% savings

## 🔍 Key Technical Details

### Cost Calculation
- **90-Day Window**: Actual costs from Azure Cost Management API
- **Monthly Average**: Total 90-day cost ÷ 3
- **Yearly Projection**: Monthly average × 12

### CPU Utilization
- **30-Day Window**: Host-level metrics from Azure Monitor
- **1-Hour Intervals**: PT1H aggregation for accuracy
- **Note**: Stopped VMs may not show metrics (especially Linux VMs)

### Pricing Logic
- **Base Pricing**: Linux VMs and Windows VMs with AHB
- **Windows Pricing**: Windows VMs without AHB (includes license cost)
- **Reserved Instance Adjustment**: For Windows without AHB, license costs are calculated separately and added to base RI pricing

### Rate Limiting
- **2-second pause** between requests when rate limited (HTTP 429)
- **Continues processing** remaining VMs instead of aborting

## 🛠️ Troubleshooting

### "No metrics available" for Linux VMs
**Cause**: VMs are currently stopped. Azure doesn't retain host-level metrics for stopped Linux VMs.  
**Solution**: Start the VMs and wait 5-10 minutes for metrics to populate, then re-run the script.

### Authentication Errors
**Solution**: Ensure you're authenticated via one of these methods:
- Azure CLI: `az login`
- Environment variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`
- Managed Identity (if running on Azure)

### Missing Cost Data
**Cause**: Insufficient permissions or VMs have no cost history yet.  
**Solution**: 
- Verify `Cost Management Reader` role assignment
- New VMs may not have cost data accumulated yet

### Empty Pricing Data
**Cause**: No VMs found or invalid subscription ID.  
**Solution**: Verify subscription ID format and access permissions.

## 📁 Project Structure

```
azure_cost_op/
├── azure_vm_cost_comparison.py   # Main script
├── price_sheet.py                # Azure Pricing API wrapper
├── requirements.txt              # Python dependencies
├── README.md                     # This file
├── vm_cost_report.html          # Generated HTML report
├── output.json                  # Generated raw data
└── pricing_data.json            # Cached pricing data
```

## 🤝 Contributing

Contributions are welcome! Please ensure all changes:
- Include proper error handling
- Maintain existing code style
- Update documentation as needed

## 📝 License

[Your License Here]

## 🙏 Acknowledgments

Built with:
- Azure SDK for Python
- Azure REST APIs (Cost Management, Monitor, Compute, Pricing)
- Modern HTML/CSS/JavaScript for reporting

---

**Made with ❤️ for Azure cost optimization**