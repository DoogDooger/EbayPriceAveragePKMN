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
from scipy import stats
import numpy as np

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

@st.cache_data(ttl=600)  # Cache for 10 minutes
def fetch_ebay_data(data, include_shipping, sale_type, listing_count, quantity_mode, grading_companies=[], exclude_outliers=False):
    """Fetches eBay data item by item and includes individual listings in the results."""
    results = []
    averages = []  # Store average prices for each item
    
    # Define the complete list of grading companies to filter
    all_grading_companies = ["PSA", "BECKETT", "BGS", "CGC", "SGC", "AGS", "TAG", "ACE", "PG", "GET GRADED"]
    
    progress_bar = st.progress(0)
    total_items = len(data)
    
    for i, (_, row) in enumerate(data.iterrows()):
        # Update progress
        progress_text = f"Fetching data for item {i+1} of {total_items}: {row['Item']}"
        st.write(progress_text)
        progress_bar.progress((i+1)/total_items)
        
        # Fetch data
        item_name = row["Item"]
        quantity = row.get("Quantity", 1)  # Default to 1 if no quantity is provided

        # Fetch active listings only
        avg_price, prices, links, titles, warning = get_active_listings(
            item_name, 
            include_shipping=include_shipping, 
            sale_type=sale_type, 
            grading_companies=grading_companies,
            all_grading_companies=all_grading_companies,
            exclude_outliers=exclude_outliers  # Pass the new parameter
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
    
    progress_bar.empty()  # Clear the progress bar when done
    return averages, results

def get_access_token():
    """Generates a new access token using the refresh token."""
    try:
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
        elif response.status_code == 401:
            raise Exception("Authentication failed. Please check your eBay API credentials.")
        else:
            raise Exception(f"Error refreshing access token: {response.json()}")
    except requests.exceptions.ConnectionError:
        st.error("Network connection error. Please check your internet connection and try again.")
        st.stop()

def base64_credentials():
    """Generates Base64-encoded credentials."""
    credentials = f"{EBAY_API_CLIENT_ID}:{EBAY_API_CLIENT_SECRET}"
    return base64.b64encode(credentials.encode()).decode()

def get_active_listings(item_name, include_shipping, sale_type, grading_companies=[], all_grading_companies=None, exclude_outliers=False):
    """Fetch active listings from eBay using the Browse API with pagination."""
    try:
        # Use default list if none provided
        if all_grading_companies is None:
            all_grading_companies = ["PSA", "BECKETT", "BGS", "CGC", "SGC", "AGS", "TAG", "ACE"]
        
        # Words to exclude from search results
        excluded_words = ["Magnetic", "Stand", "Proxy", "Custom", "Box", "Playmat"]
        
        # Check if item name contains promo types
        item_lower = item_name.lower()
        is_promo_search = "promo" in item_lower
        is_pokemon_center_promo = any(pc in item_lower for pc in ["pokemon center promo", "pokemon centre promo"])

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
        all_conditions = []  # Add this line to store condition descriptions
        offset = 0
        limit = 50  # eBay API allows up to 50 items per page

        while True:
            # Add pagination parameters
            url = f"{base_url}&limit={limit}&offset={offset}"

            # Make the API request
            response = requests.get(url, headers=headers)

            if response.status_code == 429:  # Too Many Requests
                st.warning("eBay API rate limit reached. Waiting before retrying...")
                time.sleep(5)  # Wait 5 seconds before retrying
                response = requests.get(url, headers=headers)
                
            if response.status_code != 200:
                error_message = response.json().get('errors', [{'message': 'Unknown error'}])[0].get('message', 'Unknown error')
                raise Exception(f"eBay API Error: {error_message} (Status code: {response.status_code})")

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
                
                # Extract condition information - less invasive debug approach
                condition_description = ""
                condition_display_name = "Unknown"
                if "condition" in item:
                    condition_info = item["condition"]
                    
                    # Extract condition safely
                    if isinstance(condition_info, dict):
                        condition_display_name = condition_info.get("conditionDisplayName", "Unknown")
                        condition_description = condition_info.get("conditionDescription", "")
                    else:
                        condition_description = str(condition_info)
                else:
                    condition_description = ""

                # Store data without excessive debugging output
                all_prices.append(price)
                all_links.append(item.get("itemWebUrl", "").replace("ebay.com", "ebay.co.uk"))
                all_titles.append(item.get("title", ""))
                # Store condition information for filtering - include display name as well
                all_conditions.append(f"{condition_display_name} {condition_description}".lower())

            # Check if there are more pages
            if "next" not in response_data:
                break
            offset += limit  # Move to the next page

        # After collecting all listings but before filtering

        # Filter results locally 
        filtered_prices = []
        filtered_links = []
        filtered_titles = []

        for price, link, title, condition in zip(all_prices, all_links, all_titles, all_conditions):
            title_lower = title.lower()
            should_include = True
            
            # Step 1: Check excluded words in title and condition
            if any(word.lower() in title_lower for word in excluded_words) or any(word.lower() in condition for word in excluded_words):
                should_include = False
                continue
                
            # Step 2: Handle promo-specific filtering
            if is_promo_search:
                # Item must have "promo" in title
                if "promo" not in title_lower:
                    should_include = False
                    continue
                    
                # Special case for Pokemon Center promo searches
                if is_pokemon_center_promo:
                    # Must have either "pokemon center" or "pokemon centre" in the title
                    if not any(pc in title_lower for pc in ["pokemon center", "pokemon centre"]):
                        should_include = False
                        continue
                # Regular promo searches (NOT Pokemon Center promo)
                else:
                    # Must NOT contain Pokemon Center/Centre - MORE THOROUGH CHECK
                    # Normalize accented characters for comparison
                    normalized_title = title_lower.replace("é", "e").replace("è", "e")
                    
                    # Check for various Center/Centre patterns with both normalized and original text
                    center_patterns = [
                        "pokemon center", "pokemon centre", 
                        "center stamped", "centre stamped", 
                        "pc stamped", "pc stamp", 
                        "center pc", "centre pc",
                        "pokémon center", "pokémon centre"  # Explicit check with accented é
                    ]
                    
                    has_center_phrase = any(pattern in normalized_title or pattern in title_lower 
                                           for pattern in center_patterns)
                    
                    if has_center_phrase:
                        should_include = False
                        continue

            # Step 3: Handle grading company filtering
            combined_text = title_lower + " " + condition  # Combine title and condition for filtering

            if not grading_companies:
                # For "Non-Graded" option
                # More intelligent check for graded cards
                is_graded = False
                
                # Skip items that are explicitly graded
                # Check for exact grading company names or explicit mention of "graded"
                # Don't match "ungraded" as "graded"
                if "graded" in combined_text and "ungraded" not in combined_text:
                    is_graded = True
                # Check for specific grading companies with space before/after to avoid partial matches
                elif any(f" {company.lower()} " in f" {combined_text} " for company in all_grading_companies):
                    is_graded = True
                # Also check for PSA followed by a number (common grading format)
                elif any(f"psa {str(i)}" in combined_text for i in range(1, 11)):
                    is_graded = True
                    
                if is_graded:
                    should_include = False
                    continue
            else:
                # For "Graded" option with specific companies selected
                # This case is working fine, keep it as is
                is_graded_by_selected = any(f" {company.lower()} " in f" {combined_text} " for company in grading_companies)
                
                if not is_graded_by_selected:
                    should_include = False
                    continue
                    
            # Step 4: Basic item matching
            if not improved_item_matching(item_name, title):
                should_include = False
                continue
                
            # If we got here, the item passed all filters
            if should_include:
                filtered_prices.append(price)
                filtered_links.append(link)
                filtered_titles.append(title)

        # Combine the filtered results into a single list of tuples
        filtered_results = list(zip(filtered_prices, filtered_links, filtered_titles))

        # Sort the filtered results by price in descending order
        filtered_results.sort(key=lambda x: x[0], reverse=True)

        # Unpack the sorted results back into separate lists
        filtered_prices, filtered_links, filtered_titles = zip(*filtered_results) if filtered_results else ([], [], [])

        # Apply outlier filtering if requested - ADD THIS CODE
        if exclude_outliers and len(filtered_prices) >= 4:
            filtered_prices, filtered_links, filtered_titles = filter_outliers(filtered_prices, filtered_links, filtered_titles)

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

def improved_item_matching(item_name, title):
    # Normalize text: lowercase and remove apostrophes
    norm_item = item_name.lower().replace("'", "")
    norm_title = title.lower().replace("'", "")
    
    # Check for exact substring match first (current approach)
    if norm_item in norm_title:
        return True
    
    # Split into tokens/words
    item_tokens = norm_item.split()
    title_tokens = norm_title.split()
    
    # Check if most of the search terms appear in the title
    matches = 0
    for token in item_tokens:
        # Special handling for card numbers (e.g., 183/159)
        if '/' in token and any('/' in t for t in title_tokens):
            card_numbers = [t for t in title_tokens if '/' in t]
            if any(token == num for num in card_numbers):
                matches += 1
                continue
        
        # Regular token matching
        if token in title_tokens or any(token in t for t in title_tokens):
            matches += 1
    
    # Return True if most tokens match (e.g., 75% or more)
    match_ratio = matches / len(item_tokens)
    return match_ratio >= 0.75  # Adjust threshold as needed

def filter_outliers(prices, links, titles):
    """Filter out price outliers using IQR method with more aggressive filtering."""
    if len(prices) < 4:  # Need at least 4 data points for meaningful outlier detection
        return prices, links, titles
        
    # Convert to numpy array for stats operations
    prices_array = np.array(prices)
    
    # Calculate Q1, Q3 and IQR
    q1 = np.percentile(prices_array, 25)
    q3 = np.percentile(prices_array, 75)
    iqr = q3 - q1
    
    # Define outlier boundaries (use 1.5 for standard outliers, but we'll use a tighter bound)
    # For more aggressive filtering, reduce the multiplier from 1.5 to 1.0
    lower_bound = q1 - 1.0 * iqr
    upper_bound = q3 + 1.0 * iqr
    
    # Filter out outliers
    filtered_indices = []
    for i, price in enumerate(prices):
        if lower_bound <= price <= upper_bound:
            filtered_indices.append(i)
    
    # If filtering removed too many results (less than 3), use a less aggressive approach
    if len(filtered_indices) < 3 and len(prices) > 3:
        # Try again with standard 1.5 IQR
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        filtered_indices = [i for i, price in enumerate(prices) if lower_bound <= price <= upper_bound]
    
    # Extract non-outlier data
    filtered_prices = [prices[i] for i in filtered_indices]
    filtered_links = [links[i] for i in filtered_indices]
    filtered_titles = [titles[i] for i in filtered_indices]
    
    return filtered_prices, filtered_links, filtered_titles

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
exclude_outliers = st.checkbox("Exclude outliers")  # Add this checkbox
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
    # Check if input is empty
    if input_mode == "Paste Mode" and not user_input.strip():
        st.error("No items entered. Please enter items then try again.")
    elif input_mode == "CSV Mode" and not uploaded_file:
        st.error("No CSV file uploaded. Please upload a file then try again.")
    else:
        with st.spinner("Fetching data from eBay..."):
            try:
                # Parse input
                if input_mode == "Paste Mode":
                    data = parse_input(user_input, quantity_mode)
                    
                    # Check if parsing resulted in empty dataframe
                    if data.empty:
                        st.error("No valid items found in input. Please check format and try again.")
                        st.stop()
                        
                elif input_mode == "CSV Mode" and uploaded_file:
                    data = pd.read_csv(uploaded_file)
                    if quantity_mode == "No Quantity":
                        data = data[["Item"]]  # Keep only the Item column
                    
                    # Check if CSV has required columns
                    if "Item" not in data.columns:
                        st.error("CSV file must contain an 'Item' column. Please fix and try again.")
                        st.stop()
                    
                    # Check if CSV has any rows
                    if data.empty:
                        st.error("CSV file contains no data. Please add items and try again.")
                        st.stop()

                    # For CSV input when quantity is expected
                    if quantity_mode == "Quantity":
                        # Check if Quantity column is present and contains valid numbers
                        if "Quantity" not in data.columns:
                            st.error("CSV file must contain a 'Quantity' column when Quantity mode is selected.")
                            st.stop()
                        
                        # Verify all quantities are valid numbers
                        try:
                            data["Quantity"] = data["Quantity"].astype(int)
                        except ValueError:
                            st.error("All values in the Quantity column must be valid integers.")
                            st.stop()

                # Fetch eBay data (Active Listings only)
                averages, results = fetch_ebay_data(
                    data, 
                    include_shipping=include_shipping, 
                    sale_type=sale_type, 
                    listing_count=listing_count, 
                    quantity_mode=quantity_mode, 
                    grading_companies=grading_companies,
                    exclude_outliers=exclude_outliers
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