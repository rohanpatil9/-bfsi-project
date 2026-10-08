import os

from dotenv import load_dotenv
from google import genai

load_dotenv("backend/.env")

project = os.getenv("GOOGLE_CLOUD_PROJECT")
location = os.getenv("GOOGLE_CLOUD_LOCATION")
model = os.getenv("GEMINI_MODEL")

print("Project:", project)
print("Location:", location)
print("Model:", model)

client = genai.Client(
    vertexai=True,
    project=project,
    location=location,
)

response = client.models.generate_content(
    model=model,
    contents="Reply with exactly: Gemini connection successful",
)

print(response.text)