#!/usr/bin/env python3
import requests
import json


def build_pricing_json(json_data, table_data):
    table_data.extend(json_data['Items'])
def main(regions, skus):
    table_data = []
    armRegions = ' or '.join([f"armRegionName eq '{region}'" for region in regions]).strip()
    skus = 'or '.join([f"armSkuName eq '{sku}'" for sku in skus]).strip()
    query = f"serviceName eq 'Virtual Machines' and ({armRegions}) and ({skus})"
    api_url = "https://prices.azure.com/api/retail/prices"
    if query:
        print(f"Running with query: {query}")

        response = requests.get(api_url, params={'$filter': query})
        if response.status_code == 400:
            print("Invalid query provided. Please check the syntax and try again.")
            print(response.text)
            return
    else:
        print("Running without query")
        response = requests.get(api_url)
        print(response.status_code)
    json_data = json.loads(response.text)
    
    build_pricing_json(json_data, table_data)
    nextPage = json_data['NextPageLink']
    
    while(nextPage):
        response = requests.get(nextPage)
        json_data = json.loads(response.text)
        nextPage = json_data['NextPageLink']
        build_pricing_json(json_data, table_data)

    return table_data
    
def format_data(data):
    data_dict = {}

    for each in data:
        region = each['armRegionName']
        sku = each['armSkuName']
        product = each['productName']
        sku_name = each['skuName']

        if region not in data_dict:
            data_dict[region] = {}

        if sku not in data_dict[region]:
            data_dict[region][sku] = {}
        
        if product not in data_dict[region][sku]:
            data_dict[region][sku][product] = {}
        
        if sku_name not in data_dict[region][sku][product]:
            data_dict[region][sku][product][sku_name] = {}

        sku_entry = data_dict[region][sku][product][sku_name]

        if each['type'] == 'Consumption':
            sku_entry['payg'] = each['retailPrice']
            sku_entry['payg1Month'] = f"{float(each['retailPrice']) * 24 * 31:.2f}"
            sku_entry['payg1Year'] = f"{float(each['retailPrice']) * 24 * 365:.2f}"
        elif each['type'] == 'DevTestConsumption':
            sku_entry['devtest'] = each['retailPrice']
        elif each['type'] == 'Reservation':
            term = each.get('reservationTerm', '')
            if term == '1 Year':
                sku_entry['1year'] = each['retailPrice']
            elif term == '3 Years':
                sku_entry['3year'] = each['retailPrice']
    # with open(f'{file_name}.json', 'w') as f:
    #     json.dump(data_dict, f, indent=4)
    return data_dict        



def get_pricing(regions, skus):
    data = main(regions, skus)
    current_pricing = format_data(data)
    # with open('more_pricing.json', 'w') as f:
    #     json.dump(current_pricing, f, indent=4)
    return current_pricing

if __name__ == "__main__":
    get_pricing()
