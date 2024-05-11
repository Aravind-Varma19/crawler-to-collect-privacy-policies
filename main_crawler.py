from datetime import datetime
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup, Comment
from urllib.robotparser import RobotFileParser
from time import time, sleep
from tqdm import tqdm
from queue import PriorityQueue
import os
import json 
import glob
import re
import sys
from elasticsearch7 import Elasticsearch, helpers
import pickle


es = Elasticsearch("http://localhost:9200/")
print(es.ping())
session = requests.Session()

def safe_request(url, max_retries=3, backoff_factor=0.5):
    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=10, allow_redirects=True)
            response.raise_for_status()  # Raises a HTTPError for non-200 responses
            return response
        except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if e.response.status_code == 404:
                return None  # Treat 404 as a special case to handle later
            sleep(backoff_factor * (2 ** attempt))
        except requests.exceptions.RequestException:
            sleep(backoff_factor * (2 ** attempt))
    return None
canonicalize_url_map = {}
def canonicalize_url(url, base_url=None):
    if url in canonicalize_url_map:
        return canonicalize_url_map[url]
    url = url.lower()
    parts = url.split("://", 1)
    if len(parts) == 2:
        protocol, rest = parts
    else:
        protocol, rest = "", parts[0]
 
    parts = rest.split("/", 1)
    host = parts[0]
    path = "/"
    if len(parts) == 2:
        if parts[1] == "":
            host = parts[0].split("#")[0]
        path = "/" + parts[1]
 
    host_parts = host.split(":")
    hostname = host_parts[0]
    if len(host_parts) == 2 and host_parts[1].isdigit():
        port = int(host_parts[1])
    else:
        port = None
 
    if (protocol == "http" and port == 80) or (protocol == "https" and port == 443):
        host = hostname
    else:
        if port:
            host = ":".join([hostname, str(port)])
        else:
            host = hostname
 
    if base_url and not protocol:
        base_parts = base_url.split("://", 1)
        base_protocol = base_parts[0]
        base_rest = base_parts[1] if len(base_parts) == 2 else ""
        base_host = base_rest.split("/", 1)[0]
        url = urljoin(base_protocol + "://" + base_host, url)
        return canonicalize_url(url)
 
    path = path.split("#")[0]    
    path = "/"+"/".join([part for part in path.split("/") if part])
    canonicalized_url = protocol + "://" + host + path
    if not canonicalized_url.startswith('http') or 'javascript' in canonicalized_url or 'pdf' in canonicalized_url or 'svg' in canonicalized_url or 'jpg' in canonicalized_url or 'png' in canonicalized_url or 'gif' in canonicalized_url or 'jpeg' in canonicalized_url:
        canonicalize_url_map[url] = None    
        return None
    canonicalize_url_map[url] = canonicalized_url
    return canonicalized_url
def fetch_html(url, retries=3, backoff_factor=0.5):
    response = safe_request(url, max_retries=retries, backoff_factor=backoff_factor)
    if response and 'text/html' in response.headers.get('Content-Type', ''):
        final_url = response.url  # Capture the final URL after redirections
        return {
            'html_content': response.text,
            'final_url': final_url
        }
    else:
        if response:
            print(f"Non-200 status code received: {response.status_code} for URL: {url}")
        return None
    
import re

def preprocess_text(text):
    text = re.sub(r'\s+', ' ', text)  
    # text = re.sub(r'[^\w\s]', '', text) 
    text = re.sub(r'[^\w\s\-\'’]', '', text)
    text = re.sub(r'[»«|]', '', text)
    return text.strip()

def parse_html(html_content, url):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    links = [a['href'] for a in soup.find_all('a', href=True)]
    title = soup.title.string if soup.title else ''
    text = ' '.join(soup.stripped_strings)
    text = preprocess_text(text)

    # Attempt to extract terms and conditions
    terms_text = extract_terms_and_conditions(soup)

    return links, title, text, terms_text

def extract_terms_and_conditions(soup):
    # Terms and conditions are often located under specific headings or links
    terms_headings = ['legal', 'privacy policy']
    
    terms_text = ""
    for heading in terms_headings:
        for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'a', 'div'], string=re.compile(heading, re.I), limit=1):
            
            # Attempt to follow link if 'a' tag is found
            if element.name == 'a' and element.has_attr('href'):
                terms_url = element['href']
                if not terms_url.startswith('http'):
                    # Handle relative URLs
                    terms_url = urlparse.urljoin(url, terms_url)
                terms_content = fetch_html(terms_url)
                
                if terms_content:
                    terms_soup = BeautifulSoup(terms_content['html_content'], 'html.parser')
                    terms_text += ' '.join(terms_soup.stripped_strings)
            else:
                # Extract text from a section if it's not a link
                next_node = element.find_next_sibling()
                while next_node and next_node.name not in ['h1', 'h2', 'h3', 'h4', 'h5', 'div']:
                    terms_text += next_node.get_text(strip=True) + " "
                    next_node = next_node.find_next_sibling()

    return terms_text

def preprocess_text(text):
    # Placeholder for your text preprocessing logic
    return text.strip()

def store_document(url, text, raw_html, outlinks, title):
    document = {
        'url': url,
        'text': text,
        'raw_html': raw_html,
        'outlinks': outlinks,
        'title': title,
        'timestamp': datetime.now()
    }
    es.index(index="web-crawl", document=document)
    robots_cache = {}

def check_robots_txt(url, retries=2, backoff_factor=0.5):
    parsed_url = urlparse(url)
    domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    if domain in robots_cache:
        rp = robots_cache[domain]
    else:
        robots_url = f"{domain}/robots.txt"
        rp = RobotFileParser()
        response = safe_request(robots_url, max_retries=retries, backoff_factor=backoff_factor)
        
        if response is None:  # Handle 404 for robots.txt by allowing crawling
            print(f"robots.txt not found at {robots_url}, assuming full access allowed.")
            rp.allow_all = True
        elif response and response.status_code == 200:
            rp.parse(response.text.splitlines())
        else:
            print(f"Unable to fetch robots.txt for {url}, proceeding with caution.")
            rp.allow_all = True  # Default behavior when fetch fails
        
        robots_cache[domain] = rp  # Cache the parsed rules or the decision to allow all
    
    # Check if the URL is allowed by the cached robots.txt rules
    return rp.can_fetch('*', url) if rp else True
last_request_time = {}

def throttle_request(domain):
    now = time()
    if domain in last_request_time:
        elapsed = now - last_request_time[domain]
        if elapsed < 1:
            sleep(1 - elapsed)
    last_request_time[domain] = time()
def calculate_score(url, title, text, keywords, base_score=1):
    keyword_score = sum([title.count(keyword) + text.count(keyword) for keyword in keywords])
    # Assume a simple scoring model where each keyword match increases the score
    return base_score + keyword_score

def count_keyword_matches(text, keywords):
    total_matches = 0
    # Prepare the text for case-insensitive matching
    text_lower = text.lower()
    
    for keyword in keywords:
        # Use a regular expression to find whole word matches, case-insensitive
        pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
        matches = re.findall(pattern, text_lower)
        total_matches += len(matches)
    
    return total_matches
# Priority Queue for the Frontier
frontier = PriorityQueue()
visited_urls = set()
link_graph = {}

def add_to_frontier(url, title, keywords, base_score=1):    
    # Calculate score based on keyword matches in the URL and title
    keyword_score = sum([title.count(keyword) + url.count(keyword) for keyword in keywords])
    score = base_score + keyword_score
    
    # Use negative score as priority to ensure that PriorityQueue treats higher scores as higher priority
    priority = -score
    frontier.put((priority, url))

def update_link_graph(url, inlinks=None, outlinks=None, wave_number=0, text="", keywords=[]):
    inlink_count = len(inlinks) if inlinks else 0
    keyword_matches = count_keyword_matches(text, keywords)
    
    if url not in link_graph:
        link_graph[url] = {
            "inlinks": set(inlinks) if inlinks else set(),
            "outlinks": set(outlinks) if outlinks else set(),
            "wave_number": wave_number,
            "score": calculate_score(inlink_count, wave_number, keyword_matches)
        }
    else:
        existing_entry = link_graph[url]
        if inlinks:
            existing_entry["inlinks"].update(inlinks)
        if outlinks:
            existing_entry["outlinks"].update(outlinks)
        # Recalculate the score with updated values
        existing_entry["score"] = calculate_score(len(existing_entry["inlinks"]), wave_number, keyword_matches)
def format_document(url, title, text,terms):
    return f"<DOC>\n<DOCNO>{url}</DOCNO>\n<TERMS>{terms}</TERMS>\n<HEAD>{title}</HEAD></DOC>\n"

def write_document_single(file_path, document):
    with open(file_path, 'w') as file:
        file.write(document)
keywords = [
    "Privacy",
    "Legal",
    "Policy",
    "Compliance",
    "Security",
    "Copyright",
    "Disclaimer",
    "Ethics",
    "Regulations",
    "Guidelines",
    "Safety",
    "Data",
    "Usage",
    "Agreement",
    "License",
    "Conditions",
    "Cookies",
    "GDPR",  # European privacy law
    "HIPAA"  # US healthcare compliance
]
def initialize_frontier_with_seeds(seed_urls, keywords):
    for url in seed_urls:
        add_to_frontier(url, "", keywords)
        


def store_link_graph(link_graph, file_path):
    with open(file_path, 'w') as f:
        json.dump(link_graph, f)
link_graph = {}
visited_urls = set()

def main_crawl(start_urls, target_count, keywords):
    doc_counter = 343  # Initialize document counter
    

    initialize_frontier_with_seeds(start_urls, keywords)
    print("Initialized frontier with seed URLs.")

    with tqdm(total=target_count) as pbar:
        while not frontier.empty() and len(visited_urls) < target_count:
            try:
                priority, current_url = frontier.get(False)  # Fetch the current URL from the frontier without blocking
                
                if current_url in visited_urls:  # Skip if the URL has already been crawled
                    continue

                if not check_robots_txt(current_url):  # Proceed only if robots.txt allows crawling
                    continue

                throttle_request(urlparse(current_url).netloc)  # Respect crawl delay
                content = fetch_html(current_url)  # Fetch content and outlinks
                
                if content and content['html_content']:
                    visited_urls.add(current_url)
                    links, title, text,terms = parse_html(content['html_content'], content['final_url'])

                    # Update link_graph with inlinks and outlinks, ensuring they are lists
                    if current_url not in link_graph:
                        link_graph[current_url] = {'inlinks': [], 'outlinks': [link for link in links]}
                    else:
                        link_graph[current_url]['outlinks'].extend([link for link in links if link not in link_graph[current_url]['outlinks']])

                    for link in links:
                        canonical_link = canonicalize_url(link, content['final_url'])
                        if canonical_link and canonical_link not in visited_urls:
                            add_to_frontier(canonical_link, title, keywords)  # Use keyword scoring for added URLs
                            # Update inlinks for the canonical link, ensuring it's a list
                            if canonical_link not in link_graph:
                                link_graph[canonical_link] = {'inlinks': [current_url], 'outlinks': []}
                            else:
                                if current_url not in link_graph[canonical_link]['inlinks']:
                                    link_graph[canonical_link]['inlinks'].append(current_url)

                    # Document handling
                    if terms:
                        doc_counter += 1
                        doc_file_name = f"document_{doc_counter}.txt"
                        doc_file_path = os.path.join("/Users/vikashmediboina/Projects/Aravind_scrapper/privacy", doc_file_name)  # Ensure directory exists
                        formatted_doc = format_document(content['final_url'], title, text,terms)
                        write_document_single(doc_file_path, formatted_doc)

                    pbar.update(1)
                else:
                    print(f"No content fetched for: {current_url}")
            except Exception as e:
                print(f"Error processing {current_url}: {e}")

            store_link_graph(link_graph, "/Users/vikashmediboina/Projects/Aravind_scrapper/Results/graph.json")
# Call main_crawl with seed URLs, target count, and keywords
# https://www.worldwildlife.org/threats/effects-of-climate-change
# 'https://en.wikipedia.org/wiki/Effects_of_climate_change_on_biodiversity'
seed_urls = ['https://policy.medium.com/medium-privacy-policy-f03bf92035c9', 'https://stackoverflow.com/legal/privacy-policy', 'https://www.facebook.com/privacy/policy/']
main_crawl(seed_urls, 3500, keywords)

# Assuming Elasticsearch is running locally and the index name is 'myindex'
index_name = "privacy-policy"
new_cloud_id = "c83822bf9f204bd3928fb2b3deedf98a:dXMtY2VudHJhbDEuZ2NwLmNsb3VkLmVzLmlvOjQ0MyQzNDUyNGE2NzA3ZjI0NDM0YmRkM2E0ZWI1OTBhZDgzMCQ2ODg2ZjEwNWM4YzQ0ZWE0YjNjNzk1YzRlMDkwYWJmOQ=="
new_auth = ("elastic", "rCaLfA66eg7TNgaaqhRZd7AZ")
es = Elasticsearch(timeout=10000, cloud_id=new_cloud_id, http_auth=new_auth)

es.ping()
def get_files_in_dir(folder_path):
    # gets all names of files in directory
    file_list = os.listdir(folder_path)

    # append them to list with their full paths
    file_path_list = []
    for file in file_list:
        file_path_list.append(os.path.join(folder_path, file))

    return file_path_list


"""
Function: get_data_from_text_file()
Input: file: a single file that may contain multiple documents to be indexed
Returns: data: a list of lists; each sub-list is a line from the file
Does: reads each line of the file and appends it in a list to data
"""
def get_data_from_text_file(file):
    # declare an empty list for the data
    data = []
    for line in open(file, encoding="ISO-8859-1", errors='ignore'):
        data += [str(line)]
    return data
"""
Function: yield_docs()
Input: files: a list of each file path that we want to index (each file contains one doc)
Returns: null
Does: For each file, get the fields that we need and do some text clean up. Check if the doc is already in the corpus.
If it is, update the author and inlinks. If it isn't stage it to be indexed. 
"""
def yield_docs(files):
    unique_docs = set()
    for count, file in enumerate(tqdm(files)):
        
        # retrieve data from file
        doc = get_data_from_text_file(file)
        doc = "".join(doc)
        
        # get doc no
        docno_s = doc.find("<DOCNO>") + len("<DOCNO>") 
        docno_e = doc.find("</DOCNO>")
        docno = doc[docno_s:docno_e].strip()
        id = docno.split("/")[2]

        # get title
        title_s = doc.find("<HEAD>") + len("<HEAD>") 
        ttile_e = doc.find("</HEAD>")
        title = doc[title_s:ttile_e].strip()

        # find text
        text_s = doc.find("<TERMS>") + len("<TERMS>") 
        text_e = doc.find("</TERMS>")
        text = doc[text_s:text_e].strip()
        text = re.sub(r'\n+', '\n', text).strip()
        text = text.lower()
        
        # text cleaning
        text_start_cut = text.find("jump to search")
        if text_start_cut != -1:
            text = text[text_start_cut+len("jump to search"):]
        text_end_cut3 = text.find("sources[edit]")
        if text_end_cut3 != -1:
            text = text[:text_end_cut3]
        text_end_cut4 = text.find("this page was last edited")
        if text_end_cut4 != -1:
            text = text[:text_end_cut4]
        text_end_cut5 = text.find("navigation menu")
        if text_end_cut5 != -1:
            text = text[:text_end_cut5]
        text = text.replace("[edit]", " ")
        
        # push to es
        if id not in unique_docs:
            document_data = {
                'url': docno,
                'version': 1,
                'terms': text,
                'title': title,
                'timestamp': datetime.now().isoformat()  # optional, adds a timestamp
            }
            response = es.index(index=index_name, document=document_data)
            unique_docs.add(id)
            
file_path = '/Users/vikashmediboina/Projects/Aravind_scrapper/privacy'
all_files = get_files_in_dir(file_path)
print(len(all_files))
yield_docs(all_files)