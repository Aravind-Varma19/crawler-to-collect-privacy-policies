from flask import Flask, render_template, request, redirect, url_for, session
from elasticsearch7 import Elasticsearch
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'

new_index_name = "privacy-policy"
new_cloud_id = "c83822bf9f204bd3928fb2b3deedf98a:dXMtY2VudHJhbDEuZ2NwLmNsb3VkLmVzLmlvOjQ0MyQzNDUyNGE2NzA3ZjI0NDM0YmRkM2E0ZWI1OTBhZDgzMCQ2ODg2ZjEwNWM4YzQ0ZWE0YjNjNzk1YzRlMDkwYWJmOQ=="
new_auth = ("elastic", "rCaLfA66eg7TNgaaqhRZd7AZ")
es = Elasticsearch(timeout=10000, cloud_id=new_cloud_id, http_auth=new_auth)


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # Simulate user authentication
        if username == "admin" and password == "admin":
            session['logged_in'] = True
            return redirect(url_for('search'))
        else:
            return render_template('login.html', error='Invalid username or password')
    return render_template('login.html')

@app.route('/search', methods=['GET'])
def search():
    if not session.get('logged_in'):
        return render_template('index.html', error='Please enter a query.')
    query = request.args.get('query') if request.args.get('query') else ""

    # Search Elasticsearch index
    result = es.search(index=new_index_name, body={'query': {'match': {'terms': query}}}, size=200)
    hits = result['hits']['hits']
    total_hits = len(hits)
    # Extract relevant information
    search_results = []
    for hit in hits:
        doc_id = hit['_id']
        title = hit['_source']['title']
        terms = hit['_source']['terms']
        url = hit['_source']['url']
        search_results.append({'docno': url, 'title': title,'summary': terms[:250], 'terms':terms} )

    return render_template('index.html', hits=total_hits, query=query, search_results=search_results)



if __name__ == '__main__':
    app.run(debug=True)
