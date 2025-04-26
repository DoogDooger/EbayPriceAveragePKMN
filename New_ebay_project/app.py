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
            
            # Promo filtering logic:
            # 1. If searching for Pokémon Center/Centre promo, only include listings with those terms
            # 2. If searching for regular promo (but not Pokémon Center), exclude Pokémon Center/Centre promos
            # 3. If not searching for promos at all, this section doesn't apply
            
            if is_pokemon_center_promo:
                # Must contain either "pokemon center" or "pokemon centre"
                if not any(pc in title_lower for pc in ["pokemon center", "pokemon centre"]):
                    continue
                    
            elif is_promo_search:
                # If just "promo" (not Pokémon Center promo), exclude Pokémon Center/Centre promos
                if any(pc in title_lower for pc in ["pokemon center", "pokemon centre"]):
                    continue
                # Must contain "promo"
                if "promo" not in title_lower:
                    continue
            
            # Check if no grading companies selected - filter out ALL items with ANY grading company
            if not grading_companies:
                # Skip this item if it contains any grading company name
                if any(company.lower() in title_lower for company in all_grading_companies):
                    continue
            # If grading companies are selected, only include items with those specific companies
            elif not any(company.lower() in title_lower for company in grading_companies):
                continue
                
            # Basic item name matching
            if improved_item_matching(item_name, title):
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

# After imports, add CSS for better styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem !important;
        text-align: center;
        margin-bottom: 1rem;
        color: #1E88E5;
    }
    .subheader {
        font-size: 1.5rem;
        font-weight: 600;
        margin-top: 1rem;
    }
    .tooltip-icon {
        color: #9E9E9E;
        font-size: 1rem;
        margin-left: 0.3rem;
    }
    .stProgress > div > div > div > div {
        background-color: #1E88E5;
    }
    .results-container {
        margin-top: 2rem;
        padding: 1rem;
        border-radius: 5px;
        background-color: rgba(240, 240, 240, 0.3);
    }
    .filter-container {
        background-color: rgba(240, 240, 240, 0.3);
        padding: 1rem;
        border-radius: 5px;
        margin-bottom: 1rem;
    }
    /* Mobile-responsive adjustments */
    @media (max-width: 768px) {
        .main-header {
            font-size: 1.8rem !important;
        }
    }
</style>
""", unsafe_allow_html=True)

# Use a nicer header with the CSS class
st.markdown("<h1 class='main-header'>eBay Price Averager</h1>", unsafe_allow_html=True)

# Set up tab navigation
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Search"

# Navigation with radio buttons styled to look like tabs
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    st.session_state.active_tab = st.radio("", ["Search", "Results", "Help"], 
                                          index=["Search", "Results", "Help"].index(st.session_state.active_tab),
                                          horizontal=True, label_visibility="collapsed")

# Add some styling to make it look more like tabs
st.markdown("""
<style>
    div[data-testid="stHorizontalRadio"] label {
        background-color: #f0f2f6;
        padding: 10px 20px;
        border-radius: 5px 5px 0 0;
        min-width: 100px;
        text-align: center;
    }
    div[data-testid="stHorizontalRadio"] label:hover {
        background-color: #e0e2e6;
    }
    div.stRadio > div[role="radiogroup"] > label[data-baseweb="radio"] > div:first-child {
        display: none;
    }
</style>
""", unsafe_allow_html=True)

# Content based on active tab
if st.session_state.active_tab == "Search":
    # Input mode selection with working tooltips
    st.markdown("#### Input Method")
    col1, col2 = st.columns([3, 1])
    with col1:
        input_mode = st.radio("Select how to enter items:", ["Paste Mode", "CSV Mode"])
    with col2:
        # Use st.help instead of custom HTML
        st.markdown("ℹ️")
        st.caption("Paste Mode: Enter items directly in the text area.\nCSV Mode: Upload a CSV file with items.")

    # Quantity mode selection with working tooltips
    st.markdown("#### Item Quantity")
    col1, col2 = st.columns([3, 1])
    with col1:
        quantity_mode = st.radio("Do you need to track quantities?", ["No Quantity", "Quantity"]) 
    with col2:
        # Use st.help instead of custom HTML
        st.markdown("ℹ️")
        st.caption("No Quantity: Just calculate average prices.\nQuantity: Include item quantities for inventory valuation.")
    
    # Input handling in a container with styling
    with st.container():
        st.markdown("#### Enter Items")
        if input_mode == "Paste Mode":
            if quantity_mode == "Quantity":
                user_input = st.text_area("Enter items (one per line):", 
                                        placeholder="Example:\nCharizard 151/165, 2\nGengar 130/214, 1")
                st.caption("Format: item name, quantity")
            else:
                user_input = st.text_area("Enter items (one per line):", 
                                        placeholder="Example:\nCharizard 151/165\nGengar 130/214")
                st.caption("Format: item name")
        elif input_mode == "CSV Mode":
            uploaded_file = st.file_uploader("Upload a CSV file", type=["csv"])
            if quantity_mode == "No Quantity":  # Add the missing "==" comparison operator
                st.caption("CSV must contain 'Item' and 'Quantity' columns")
            else:
                st.caption("CSV must contain an 'Item' column")
    
    # Filtering options in an expander
    with st.expander("📊 Filtering Options", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            include_shipping = st.checkbox("Include shipping cost", 
                                        help="When checked, shipping costs will be added to item prices")
            exclude_outliers = st.checkbox("Exclude outliers", 
                                        help="When checked, statistical outliers will be removed from price calculations")
        with col2:
            sale_type = st.selectbox("Sale Type", 
                                    ["Buy It Now", "Auction", "Both"],
                                    help="Filter by listing type on eBay")
            listing_count = st.select_slider("Number of listings to consider", 
                                            options=[3, 5, 10], 
                                            value=5,
                                            help="How many listings to include in average calculation")
    
    # Grading options in a separate expander
    with st.expander("🏅 Grading Options", expanded=True):
        card_grading = st.radio("Card Grading", ["Graded", "Non-Graded"], 
                             help="Filter by graded or non-graded items")
        
        # Only show grading companies multiselect if "Graded" is selected
        grading_companies = []
        if card_grading == "Graded":
            grading_companies = st.multiselect(
                "Select Grading Companies to Include",
                ["PSA", "BECKETT", "CGC", "AGS", "TAG", "ACE"],
                default=["PSA", "BECKETT", "CGC", "ACE"],
                help="Only show listings graded by these companies"
            )
    
    # Refresh button with a better style
    st.markdown("")  # Add some spacing
    refresh_col1, refresh_col2, refresh_col3 = st.columns([1, 2, 1])
    with refresh_col2:
        refresh_button = st.button("🔄 Refresh Prices", use_container_width=True)

# Results tab will be populated later
elif st.session_state.active_tab == "Results":  
    st.info("Click 'Refresh Prices' on the Search tab to see results here.")

# Help tab with usage instructions
elif st.session_state.active_tab == "Help":
    st.subheader("How to Use This App")
    st.markdown("""
    ### Basic Usage
    1. **Enter your items** - Either paste them directly or upload a CSV file
    2. **Set your filtering options** - Choose shipping inclusion, sale types, etc.
    3. **Click 'Refresh Prices'** - The app will fetch current eBay listings
    4. **Review results** - Average prices and individual listings will display
    
    ### Advanced Features
    - **Outlier Detection** - Remove statistical outliers from price calculations
    - **Grading Company Filter** - Focus on specific grading companies or exclude graded items
    - **Promo Card Detection** - Special handling for promotional cards
    
    ### Tips
    - Be specific with card numbers (e.g., "Charizard 151/165" instead of just "Charizard")
    - For Pokémon Center promos, include "Pokemon Center promo" in your search term
    - Use the CSV mode for bulk processing
    """)

# In the refresh button handler:
if refresh_button:
    # Check if input is empty
    if input_mode == "Paste Mode" and not user_input.strip():
        st.error("No items entered. Please enter items then try again.")
    elif input_mode == "CSV Mode" and not uploaded_file:
        st.error("No CSV file uploaded. Please upload a file then try again.")
    else:
        # Switch to Results tab
        st.session_state.active_tab = "Results" 
        st.rerun()  # Use st.rerun() instead of st.experimental_rerun()