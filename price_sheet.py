#!/usr/bin/env python3
import requests
import json
import sys


def build_pricing_json(json_data, table_data):
    """Append pricing items from API response to table_data"""
    items = json_data.get('Items', [])
    if items:
        table_data.extend(items)


def main(regions, skus):
    """Fetch VM pricing data from Azure Retail Prices API"""
    if not regions or not skus:
        print("Warning: No regions or SKUs provided for pricing lookup")
        return []
    
    table_data = []
    arm_regions = ' or '.join([f"armRegionName eq '{region}'" for region in regions]).strip()
    arm_skus = ' or '.join([f"armSkuName eq '{sku}'" for sku in skus]).strip()
    query = f"serviceName eq 'Virtual Machines' and ({arm_regions}) and ({arm_skus})"
    api_url = "https://prices.azure.com/api/retail/prices"
    
    try:
        response = requests.get(api_url, params={'$filter': query}, timeout=30)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        print("Error: Request to Azure Pricing API timed out")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching pricing data: {e}")
        return []
    
    try:
        json_data = response.json()
    except json.JSONDecodeError as e:
        print(f"Error parsing pricing API response: {e}")
        return []
    
    build_pricing_json(json_data, table_data)
    next_page = json_data.get('NextPageLink')
    
    max_pages = 100
    page_count = 1
    
    while next_page and page_count < max_pages:
        try:
            response = requests.get(next_page, timeout=30)
            response.raise_for_status()
            json_data = response.json()
            next_page = json_data.get('NextPageLink')
            build_pricing_json(json_data, table_data)
            page_count += 1
        except requests.exceptions.RequestException as e:
            print(f"Error fetching paginated pricing data: {e}")
            break
        except json.JSONDecodeError as e:
            print(f"Error parsing paginated response: {e}")
            break
    
    if page_count >= max_pages:
        print(f"Warning: Reached maximum page limit ({max_pages}), some pricing data may be incomplete")
    
    return table_data


def format_data(data):
    """Transform pricing data into nested dictionary structure by region/SKU/product"""
    if not data:
        return {}
    
    data_dict = {}

    for item in data:
        try:
            region = item.get('armRegionName')
            sku = item.get('armSkuName')
            product = item.get('productName')
            sku_name = item.get('skuName')
            
            if not all([region, sku, product, sku_name]):
                continue

            if region not in data_dict:
                data_dict[region] = {}

            if sku not in data_dict[region]:
                data_dict[region][sku] = {}
            
            if product not in data_dict[region][sku]:
                data_dict[region][sku][product] = {}
            
            if sku_name not in data_dict[region][sku][product]:
                data_dict[region][sku][product][sku_name] = {}

            sku_entry = data_dict[region][sku][product][sku_name]
            item_type = item.get('type', '')
            retail_price = item.get('retailPrice', 0)

            if item_type == 'Consumption':
                sku_entry['payg'] = retail_price
                sku_entry['payg1Month'] = f"{float(retail_price) * 24 * 31:.2f}"
                sku_entry['payg1Year'] = f"{float(retail_price) * 24 * 365:.2f}"
            elif item_type == 'DevTestConsumption':
                sku_entry['devtest'] = retail_price
            elif item_type == 'Reservation':
                term = item.get('reservationTerm', '')
                if term == '1 Year':
                    sku_entry['1year'] = retail_price
                elif term == '3 Years':
                    sku_entry['3year'] = retail_price
        except (KeyError, TypeError, ValueError) as e:
            print(f"Warning: Skipping malformed pricing item: {e}")
            continue
    
    return data_dict


def get_pricing(regions, skus):
    """Fetch and format VM pricing data for specified regions and SKUs"""
    try:
        data = main(regions, skus)
        current_pricing = format_data(data)
        return current_pricing
    except Exception as e:
        print(f"Error getting pricing data: {e}")
        return {}


if __name__ == "__main__":
    print("This module is designed to be imported, not run directly")
    sys.exit(1)
