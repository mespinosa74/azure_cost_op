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
    # with open('VM_Pricing_v2.json', 'r') as f:
    #     pricing_data = json.load(f)
    for r in resources:
        c = cost_info.get(r['name'].lower(), {
            "total_cost_3m": 0.0,
            "active_days": 0,
            "avg_monthly_cost": 0.0,
            "one_year_est": 0.0,
            "three_year_est": 0.0,
            "is_new": True
        })
        joined.append({
            "name": r["name"],
            "region": r["location"],
            "vmSize": r["vmSize"],
            "osType": r["osType"],
            "total_cost_3m": c["total_cost_3m"],
            "avg_monthly_cost": c["avg_monthly_cost"],
            "one_year_est": c["one_year_est"],
            "three_year_est": c["three_year_est"],
            "is_new": c["is_new"],
            "price_data": pricing_data.get(r["location"], {}).get(r["vmSize"], {})

        })
    return joined

def get_pricing_list(regions, skus):
    pricing_list = price_sheet.get_pricing(regions, skus)
    
    return pricing_list

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
        sub_data[subscription] = join_data
    with open('Cost_op_data.json', 'w') as f:
        json.dump(sub_data, f, indent=4)
