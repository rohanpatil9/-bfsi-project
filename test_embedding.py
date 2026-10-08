import os

from dotenv import load_dotenv
from google import genai
from google.genai.types import EmbedContentConfig

load_dotenv("backend/.env")

project = os.getenv("GOOGLE_CLOUD_PROJECT")
location = os.getenv("GOOGLE_CLOUD_LOCATION")
model = os.getenv("EMBEDDING_MODEL")
dimension = int(os.getenv("EMBEDDING_DIM"))

print("Project:", project)
print("Location:", location)
print("Embedding model:", model)
print("Requested dimension:", dimension)

client = genai.Client(
    vertexai=True,
    project=project,
    location=location,
)

response = client.models.embed_content(
    model=model,
    contents="Alphabet revenue increased during the quarter.",
    config=EmbedContentConfig(
        task_type="RETRIEVAL_DOCUMENT",
        output_dimensionality=dimension,
    ),
)

embedding = response.embeddings[0].values

print("Actual embedding dimension:", len(embedding))
print("First 5 values:", embedding[:5])