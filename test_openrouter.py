#!/usr/bin/env python3
"""Quick smoke test: can we call OpenRouter via strands OpenAIModel?"""
import os
from dotenv import load_dotenv
load_dotenv()

from strands import Agent
from strands.models.openai import OpenAIModel

model = OpenAIModel(
    model_id="anthropic/claude-sonnet-4",
    client_args={
        "api_key": os.environ["OPENROUTER_API_KEY"],
        "base_url": os.environ["OPENROUTER_BASE_URL"],
    },
    max_tokens=256,
)

agent = Agent(model=model, system_prompt="You are a helpful assistant.")
response = agent("Say hello in one sentence.")
print("Response:", response)
print("OK - OpenRouter via strands works!")
