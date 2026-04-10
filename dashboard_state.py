import asyncio
import json

from openai import AsyncOpenAI

from config.agent_instructions import AGENT_INSTRUCTIONS

client = AsyncOpenAI()


async def run(input_data: dict) -> dict:
    agent = AGENT_INSTRUCTIONS["dashboard_state"]
    response = await client.chat.completions.create(
        model=agent["model"],
        messages=[
            {"role": "system", "content": agent["system_prompt"]},
            {"role": "user", "content": json.dumps(input_data)},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)
