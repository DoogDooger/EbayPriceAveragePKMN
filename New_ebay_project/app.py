# eBay Price Averager Streamlit App
# This app allows users to input item names and quantities, fetches average prices from eBay, and displays the results.
# It also provides options for filtering by grading companies, shipping costs, sale type, and the number of listings to consider.

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
import os
import base64
import requests
import time
from datetime import datetime, timezone
import math

# Load environment variables from .env file
load_dotenv()

# Access the credentials
EBAY_API_CLIENT_ID = os.getenv("EBAY_API_CLIENT_ID")
EBAY_API_CLIENT_SECRET = os.getenv("EBAY_API_CLIENT_SECRET")
EBAY_API_REFRESH_TOKEN = os.getenv("EBAY_API_REFRESH_TOKEN")

if not EBAY_API_CLIENT_ID or not EBAY_API_CLIENT_SECRET or not EBAY_API_REFRESH_TOKEN:
    st.error("eBay API credentials or refresh token are missing. Please set them in a .env file or as environment variables.")

# Functions
@st.cache_data
def parse_input(user_input, quantity_mode):
    """Parses user input from textarea."""
    lines = user_input.strip().split("\n")
    data = []
    for line in lines:
        try:
            if quantity_mode == "Quantity":
                # Expect "item name, quantity" format
                name, quantity = line.split(",")
                data.append({"Item": name.strip(), "Quantity": int(quantity.strip())})
            else:
                # Expect only "item name" format
                name = line.strip()
                data.append({"Item": name})
        except ValueError:
            st.warning(f"Invalid line format: {line}")
    return pd.DataFrame(data)

@st.cache_data
def fetch_ebay_data(data, include_shipping, sale_type, listing_count, quantity_mode, grading_companies=[]):
    """Fetches eBay data item by item and includes individual listings in the results."""
    results = []
    averages = []  # Store average prices for each item
    
    # Define the complete list of grading companies to filter
    all_grading_companies = ["PSA", "BECKETT", "BGS", "CGC", "SGC", "AGS", "TAG", "ACE", "PG", "GET GRADED"]
    
    for _, row in data.iterrows():
        # Fetch data
        item_name = row["Item"]
        quantity = row.get("Quantity", 1)  # Default to 1 if no quantity is provided

        # Fetch active listings only
        avg_price, prices, links, titles, warning = get_active_listings(
            item_name, 
            include_shipping=include_shipping, 
            sale_type=sale_type, 
            grading_companies=grading_companies,  # Pass selected grading companies
            all_grading_companies=all_grading_companies  # Pass ALL grading companies for filtering
        )
        
        # No need for additional filtering here as we'll handle it all in get_active_listings
        
        # Recalculate average price after filtering
        avg_price = sum(prices) / len(prices) if prices else None
        averages.append({"Item": item_name, "Unit Average Price (GBP)": avg_price, "Warning": warning})

        # Add individual listings to the results
        for price, link, title in zip(prices, links, titles):
            results.append({
                "Item": item_name,
                "Title": title,
                "Price (GBP)": price,
                "Link": link
            })

        # Add a warning if no listings are found
        if not prices:
            results.append({
                "Item": item_name,
                "Title": "No matching listings found.",
                "Price (GBP)": None,
                "Link": None
            })

    return averages, results

def get_access_token():
    """Generates a new access token using the refresh token."""
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {base64_credentials()}"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": EBAY_API_REFRESH_TOKEN,
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        raise Exception(f"Error refreshing access token: {response.json()}")

def base64_credentials():
    """Generates Base64-encoded credentials."""
    credentials = f"{EBAY_API_CLIENT_ID}:{EBAY_API_CLIENT_SECRET}"
    return base64.b64encode(credentials.encode()).decode()

def get_active_listings(item_name, include_shipping, sale_type, grading_companies=[], all_grading_companies=None):
    """Fetch active listings from eBay using the Browse API with pagination."""
    try:
        # Use default list if none provided
        if all_grading_companies is None:
            all_grading_companies = ["PSA", "BECKETT", "BGS", "CGC", "SGC", "AGS", "TAG", "ACE"]
        
        # Words to exclude from search results
        excluded_words = ["Magnetic", "Stand", "Proxy", "Custom", "Box", "Playmat"]
            
        # Get a new access token
        access_token = get_access_token()

        # Set up API request
        base_url = f"https://api.ebay.com/buy/browse/v1/item_summary/search?q={item_name}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"  # Specify the UK marketplace
        }

        # Add filters
        filters = []
        if sale_type == "Buy It Now":
            filters.append("buyingOptions:{FIXED_PRICE}")
        elif sale_type == "Auction":
            filters.append("buyingOptions:{AUCTION}")
        filters.append("itemLocationCountry:GB")  # UK-only results

        if filters:
            base_url += f"&filter={','.join(filters)}"

        # Pagination variables
        all_prices = []
        all_links = []
        all_titles = []
        offset = 0
        limit = 50  # eBay API allows up to 50 items per page

        while True:
            # Add pagination parameters
            url = f"{base_url}&limit={limit}&offset={offset}"

            # Make the API request
            response = requests.get(url, headers=headers)

            if response.status_code != 200:
                raise Exception(f"eBay API Error: {response.json()}")

            # Parse the response
            response_data = response.json()
            if "itemSummaries" not in response_data:
                break  # No more items to fetch

            # Extract listings
            for item in response_data.get("itemSummaries", []):
                title = item.get("title", "").lower()
                price = float(item["price"]["value"])
                if include_shipping and "shippingOptions" in item:
                    price += float(item["shippingOptions"][0]["shippingCost"]["value"])
                all_prices.append(price)
                all_links.append(item.get("itemWebUrl", "").replace("ebay.com", "ebay.co.uk"))
                all_titles.append(item.get("title", ""))

            # Check if there are more pages
            if "next" not in response_data:
                break
            offset += limit  # Move to the next page

        # Filter results locally
        filtered_prices = []
        filtered_links = []
        filtered_titles = []
        
        for price, link, title in zip(all_prices, all_links, all_titles):
            title_lower = title.lower()
            
            # Skip if title contains any excluded words
            if any(word.lower() in title_lower for word in excluded_words):
                continue
                
            # Check if no grading companies selected - filter out ALL items with ANY grading company
            if not grading_companies:
                # Skip this item if it contains any grading company name
                if any(company.lower() in title_lower for company in all_grading_companies):
                    continue
            # If grading companies are selected, only include items with those specific companies
            elif not any(company.lower() in title_lower for company in grading_companies):
                continue
                
            # Basic item name matching - simplify this for now
            if item_name.lower().replace("'", "") in title_lower.replace("'", ""):
                filtered_prices.append(price)
                filtered_links.append(link)
                filtered_titles.append(title)
            
        # Combine the filtered results into a single list of tuples
        filtered_results = list(zip(filtered_prices, filtered_links, filtered_titles))

        # Sort the filtered results by price in descending order
        filtered_results.sort(key=lambda x: x[0], reverse=True)

        # Unpack the sorted results back into separate lists
        filtered_prices, filtered_links, filtered_titles = zip(*filtered_results) if filtered_results else ([], [], [])

        # Restrict the number of listings to the selected count
        filtered_prices = filtered_prices[:listing_count]
        filtered_links = filtered_links[:listing_count]
        filtered_titles = filtered_titles[:listing_count]

        # Calculate the average price and round down to 2 decimal places
        avg_price = math.floor((sum(filtered_prices) / len(filtered_prices)) * 100) / 100 if filtered_prices else 0.0
        warning = "" if filtered_prices else "No matching listings found."

        # Return the filtered results
        return avg_price, filtered_prices, filtered_links, filtered_titles, warning

    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error: {e}")
    except ValueError as e:
        raise Exception(f"Error parsing response: {e}")
    except Exception as e:
        raise Exception(f"Error fetching active listings for {item_name}: {e}")

# Streamlit app logic starts here
st.title("eBay Price Averager")

# Input mode selection
input_mode = st.radio("Input Mode", ["Paste Mode", "CSV Mode"])

# Toggle between Quantity and No Quantity modes
quantity_mode = st.radio("Quantity Mode", ["No Quantity", "Quantity"])  # Default to "No Quantity"

# Input handling
if input_mode == "Paste Mode":
    if quantity_mode == "Quantity":
        user_input = st.text_area("Enter items (one per line, format: item name, quantity):")
    else:
        user_input = st.text_area("Enter items (one per line, format: item name):")
elif input_mode == "CSV Mode":
    uploaded_file = st.file_uploader("Upload a CSV file", type=["csv"])

# Filtering options
include_shipping = st.checkbox("Include shipping cost")
sale_type = st.selectbox("Sale Type", ["Buy It Now", "Auction", "Both"])
listing_count = st.radio("Number of active listings to consider", [3, 5, 10])

# Grading filter section
card_grading = st.radio("Card Grading", ["Graded", "Non-Graded"])

# Only show grading companies multiselect if "Graded" is selected
grading_companies = []
if card_grading == "Graded":
    grading_companies = st.multiselect(
        "Select Grading Companies to Include",
        ["PSA", "BECKETT", "CGC", "AGS", "TAG", "ACE"],
        default=["PSA", "BECKETT", "CGC", "ACE"]  # Default selected companies
    )
# For "Non-Graded" option, grading_companies remains empty, which will filter out all graded cards

# Refresh button
if st.button("Refresh Prices"):
    with st.spinner("Fetching data from eBay..."):
        try:
            # Parse input
            if input_mode == "Paste Mode":
                data = parse_input(user_input, quantity_mode)
            elif input_mode == "CSV Mode" and uploaded_file:
                data = pd.read_csv(uploaded_file)
                if quantity_mode == "No Quantity":
                    data = data[["Item"]]  # Keep only the Item column

            # Fetch eBay data (Active Listings only)
            averages, results = fetch_ebay_data(
                data, 
                include_shipping=include_shipping, 
                sale_type=sale_type, 
                listing_count=listing_count, 
                quantity_mode=quantity_mode, 
                grading_companies=grading_companies  # Pass selected grading companies
            )

            # Display results
            if results:
                # Create a dictionary to organize results by item
                items_data = {}
                
                # First gather all data by item name
                for item_name in set(r["Item"] for r in results):
                    # Get average price for this item
                    avg_info = next((a for a in averages if a["Item"] == item_name), None)
                    
                    # Get listings for this item
                    item_listings = [r for r in results if r["Item"] == item_name]
                    
                    # Store data
                    items_data[item_name] = {
                        "Item": item_name,
                        "Unit Average Price (GBP)": avg_info.get("Unit Average Price (GBP)") if avg_info else None,
                        "Warning": avg_info.get("Warning", "") if avg_info else "",
                        "Listings": item_listings
                    }
                
                # Create the horizontal layout
                horizontal_results = []
                for item_name, data in items_data.items():
                    row = {
                        "Item": data["Item"],
                        "Unit Average Price (GBP)": data["Unit Average Price (GBP)"],
                        "Warning": data["Warning"]
                    }
                    
                    # Add each listing as separate columns with clickable links
                    for i, listing in enumerate(data["Listings"]):
                        price = listing.get('Price (GBP)', '')
                        title = listing.get('Title', '')
                        link = listing.get('Link', '')
                        
                        if price and title and link:
                            # Create a markdown link
                            row[f"Listing {i+1}"] = f"{price} - [{title}]({link})"
                        else:
                            row[f"Listing {i+1}"] = f"{price} - {title}"
                    
                    horizontal_results.append(row)

                # Display the horizontal table
                horizontal_df = pd.DataFrame(horizontal_results)
                st.markdown("### Results")
                st.markdown(horizontal_df.to_markdown(index=False), unsafe_allow_html=True)
                
                # Add download button for the horizontal results
                st.download_button("Download Results CSV", horizontal_df.to_csv(index=False), "results.csv")
            else:
                st.warning("No results to display.")

        except ConnectionError as e:
            st.error("Error connecting to eBay API. Please try again later.")
        except Exception as e:
            st.error(f"An unexpected error occurred: {e}")