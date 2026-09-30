import re
import json
import logging
import requests
from typing import List, Dict, Any

# Simple BM25 implementation for zero-dependency local RAG
import math
from collections import Counter

class SimpleBM25:
    def __init__(self, corpus: List[str]):
        self.corpus_size = len(corpus)
        self.avgdl = sum(len(doc.split()) for doc in corpus) / self.corpus_size if self.corpus_size > 0 else 0
        self.doc_freqs = []
        self.idf = {}
        self.doc_len = []
        
        for document in corpus:
            frequencies = Counter(document.lower().split())
            self.doc_freqs.append(frequencies)
            self.doc_len.append(sum(frequencies.values()))
            for word, freq in frequencies.items():
                self.idf[word] = self.idf.get(word, 0) + 1
                
        for word, freq in self.idf.items():
            self.idf[word] = math.log(1 + (self.corpus_size - freq + 0.5) / (freq + 0.5))

    def get_scores(self, query: str) -> List[float]:
        score = [0.0] * self.corpus_size
        query_words = query.lower().split()
        k1 = 1.5
        b = 0.75
        for q in query_words:
            if q not in self.idf:
                continue
            idf = self.idf[q]
            for idx, frequencies in enumerate(self.doc_freqs):
                if q in frequencies:
                    freq = frequencies[q]
                    doc_len = self.doc_len[idx]
                    score[idx] += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / self.avgdl))
        return score

class PIIRedactor:
    """
    Sanitizes raw student emails before vector indexing or LLM processing.
    """
    def __init__(self):
        # Basic patterns for PII
        self.ssn_pattern = re.compile(r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b')
        self.student_id_pattern = re.compile(r'\b(?:ID|Student ID)[:\s]*(\d{7,10})\b', re.IGNORECASE)
        self.phone_pattern = re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')
        self.date_pattern = re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b')
        
    def redact(self, text: str) -> str:
        # Redact student ID groups (but keep the tag)
        text = self.student_id_pattern.sub('Student ID: [STUDENT_ID]', text)
        
        text = self.ssn_pattern.sub('[SSN_REDACTED]', text)
        text = self.phone_pattern.sub('[PHONE]', text)
        text = self.date_pattern.sub('[DATE]', text)
        
        return text

class LocalRAGEngine:
    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url
        self.redactor = PIIRedactor()
        self.documents = []
        self.embeddings = []
        self.bm25 = None
    
    def _get_embedding(self, text: str) -> List[float]:
        """
        Fetches nomic-embed-text embedding from local Ollama.
        """
        try:
            response = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": "nomic-embed-text", "prompt": text},
                timeout=10
            )
            response.raise_for_status()
            return response.json().get("embedding", [])
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch embedding: {e}")
            return []

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        if not v1 or not v2: return 0.0
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm_v1 = math.sqrt(sum(a * a for a in v1))
        norm_v2 = math.sqrt(sum(b * b for b in v2))
        if norm_v1 == 0 or norm_v2 == 0: return 0.0
        return dot_product / (norm_v1 * norm_v2)

    def add_documents(self, documents: List[str]):
        """
        Pre-chunks course documents and builds dense/sparse indices.
        """
        for doc in documents:
            redacted_doc = self.redactor.redact(doc)
            self.documents.append(redacted_doc)
            self.embeddings.append(self._get_embedding(redacted_doc))
        self.bm25 = SimpleBM25(self.documents)

    def retrieve(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Retrieves top_k chunks using Reciprocal Rank Fusion (RRF) on Dense and BM25 scores.
        """
        redacted_query = self.redactor.redact(query)
        query_embedding = self._get_embedding(redacted_query)
        
        if not self.documents:
            return []

        # Sparse scores
        bm25_scores = self.bm25.get_scores(redacted_query)
        
        # Dense scores
        dense_scores = []
        for doc_emb in self.embeddings:
            dense_scores.append(self._cosine_similarity(query_embedding, doc_emb))
            
        # RRF (Reciprocal Rank Fusion)
        def get_ranks(scores):
            sorted_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            ranks = {idx: rank + 1 for rank, idx in enumerate(sorted_indices)}
            return ranks
            
        sparse_ranks = get_ranks(bm25_scores)
        dense_ranks = get_ranks(dense_scores)
        
        rrf_scores = []
        k = 60
        for i in range(len(self.documents)):
            score = 1.0 / (k + sparse_ranks[i]) + 1.0 / (k + dense_ranks[i])
            # Use dense_scores for max_relevance since BM25 is unbounded
            max_rel = dense_scores[i]
            rrf_scores.append((i, score, max_rel))
            
        rrf_scores.sort(key=lambda x: x[1], reverse=True)
        
        results = []
        for idx, score, max_rel in rrf_scores[:top_k]:
            results.append({
                "content": self.documents[idx],
                "score": score,
                "max_relevance": max_rel
            })
        
        # Ensure we add the uncertain flag if max_relevance is below threshold
        if results and results[0]["max_relevance"] < 0.60:
            results[0]["uncertain"] = True
            
        return results
