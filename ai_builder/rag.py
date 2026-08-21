import os
import chromadb
from typing import List

_DB_DIR = "chroma_db"
client = chromadb.PersistentClient(path=_DB_DIR)

# Get or create collection
collection = client.get_or_create_collection(name="brand_context")

def add_document(doc_id: str, text: str):
    """Add a document to the RAG vector store."""
    collection.upsert(
        documents=[text],
        ids=[doc_id]
    )

def search_context(query: str, n_results: int = 2) -> List[str]:
    """Retrieve relevant documents for a given query."""
    if collection.count() == 0:
        return []
    
    # We cap n_results to the collection count to avoid errors
    n = min(n_results, collection.count())
    
    results = collection.query(
        query_texts=[query],
        n_results=n
    )
    
    if results["documents"]:
        return results["documents"][0]
    return []

# Seed some dummy brand data if empty
if collection.count() == 0:
    add_document("guideline_1", "Brand Voice: Always be friendly and concise. Refer to the store as 'Flow Coffee'.")
    add_document("guideline_2", "Discount Policy: If messaging churned users (no visits in 3 months), offer a 15% discount code 'COMEBACK15'.")
    add_document("guideline_3", "Safety Gate: Never send SMS blasts to more than 50 users without inserting a Human-in-the-Loop 'Approval' node before the action.")
