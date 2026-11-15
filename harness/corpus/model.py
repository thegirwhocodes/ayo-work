"""Cohere Embed v4 LangChain adapter with explicit dimensions and input types."""
import os
from langchain_core.embeddings import Embeddings


class CohereSupportEmbeddings(Embeddings):
    def __init__(self, api_key):
        import cohere
        self.client = cohere.ClientV2(api_key=api_key)

    def _embed(self, texts, input_type):
        result = []
        for start in range(0, len(texts), 96):
            response = self.client.embed(model="embed-v4.0", texts=texts[start:start + 96],
                input_type=input_type, output_dimension=1024, embedding_types=["float"], truncate="NONE")
            result.extend(response.embeddings.float_)
        return result

    def embed_documents(self, texts):
        return self._embed(texts, "search_document")

    def embed_query(self, text):
        return self._embed([text], "search_query")[0]


def cohere_embeddings():
    return CohereSupportEmbeddings(os.environ["COHERE_API_KEY"])

