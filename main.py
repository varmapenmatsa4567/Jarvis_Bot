from openai import OpenAI
import os

client = OpenAI(
    base_url="https://aiapiv2.pekpik.com/v1",
    api_key="sk-ceaWeoE6jZEMEEo6lSPXgjd5WIWA6OSoeIrzCa2Fw4n5hEDF"
)

response = client.chat.completions.create(
    model="claude-opus-4-7",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)