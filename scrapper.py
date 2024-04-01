import requests
from bs4 import BeautifulSoup
from elasticsearch import Elasticsearch
import json
es = Elasticsearch("http://localhost:9200")
def fetch_privacy_policy(url):
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')

        # Attempt to find the privacy policy link
        privacy_link = None
        for a in soup.find_all('a', href=True):
            if 'privacy' in a.text.lower():
                privacy_link = a['href']
                break

        # Make the link absolute if it is relative
        if privacy_link and not privacy_link.startswith(('http', 'https')):
            privacy_link = requests.compat.urljoin(url, privacy_link)

        # Fetch the privacy policy
        if privacy_link:
            privacy_response = requests.get(privacy_link)
            privacy_soup = BeautifulSoup(privacy_response.content, 'html.parser')
            return privacy_soup.get_text()
        else:
            return "Privacy policy link not found."
    except Exception as e:
        return f"Error fetching privacy policy: {str(e)}"

def index_privacy_policy(es, url, content):
    # Define the document structure
    doc = {
        "url": url,
        "content": content
    }
    
    # Index the document in the 'privacy_policies' index
    response = es.index(index="privacy_policies", document=doc)
    return response


def save_to_json(file_path, data):
    # Attempt to load existing data, or initialize an empty list if the file doesn't exist or is empty
    try:
        with open(file_path, 'r') as file:
            try:
                existing_data = json.load(file)
            except json.JSONDecodeError:
                existing_data = []
    except FileNotFoundError:
        existing_data = []

    # Append the new data
    existing_data.append(data)

    # Write back to the file
    with open(file_path, 'w') as file:
        json.dump(existing_data, file, indent=4)

def ensure_index_exists(es):
    # Check if the index exists
    if not es.indices.exists(index="privacy_policies"):
        # Create the index if it doesn't exist
        es.indices.create(index="privacy_policies", ignore=400)  # ignore 400 to not fail if it already exists

def main():
    # Elasticsearch connection
    
    
    # Ensure the index exists
    # ensure_index_exists(es)
    
    # Replace 'urls.txt' with the path to your file
    with open('urls.txt', 'r') as file:
        urls = file.read().splitlines()
    json_file_path = 'privacy_policies.json'
    for url in urls:
        print(f"Fetching privacy policy for {url}")
        privacy_policy_text = fetch_privacy_policy(url)
        
        # Index the fetched privacy policy into Elasticsearch
        if privacy_policy_text not in ["Privacy policy link not found.", ""]:
            # response = index_privacy_policy(es, url, privacy_policy_text)
            data = {"url": url, "content": privacy_policy_text}
            save_to_json(json_file_path, data)
            # print(f"Indexed Privacy Policy for {url}. Response ID: {response['_id']}")
        else:
            print(f"Failed to fetch or index privacy policy for {url}")

        print("\n" + "#" * 80 + "\n")

if __name__ == "__main__":
    main()
