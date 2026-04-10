# Parallel Agentic Workflow (Restaurant AI)

Pure Python backend: parallel OpenAI agent calls for SMS/chat classification, NLU, customer replies, cashier dashboard updates, QA evaluation, and orchestrated approval with regeneration.

## Install

```bash
pip install -r requirements.txt
```

## Configure

1. Copy `.env.example` to `.env`.
2. Set your API key in `.env`:

```bash
OPENAI_API_KEY=sk-...
```

## Run

```bash
python main.py
```

## Adapt for a new project

Open `config/agent_instructions.py` and rewrite the `system_prompt` field (and optionally `role`, `model`, and `output_format`) for each agent. **No other files need to change** for a different domain or tone.

## Architecture (parallel stages)

Plain-text flow:

```
                    +------------------+
                    |  customer input  |
                    +--------+---------+
                             |
              +--------------+--------------+
              |    STAGE 1 (parallel)       |
              |  asyncio.gather(            |
              |    classifier,            |
              |    nlu_context            |
              |  )                        |
              +--------------+--------------+
                             |
                             v
              +--------------+--------------+
              |   enriched payload merge    |
              | (classifier + NLU + history)|
              +--------------+--------------+
                             |
         +-------------------+-------------------+
         |         STAGE 2 (parallel)           |
         |  asyncio.gather(                      |
         |    response_generator,               |
         |    dashboard_state                   |
         |  )                                   |
         +-------------------+-------------------+
                             |
                             v
              +--------------+--------------+
              |   STAGE 3: evaluator        |
              |   (single call)             |
              +--------------+--------------+
                             |
                             v
              +--------------+--------------+
              |   orchestrator              |
              |   (approval / regen)        |
              +--------------+--------------+
                             |
              +--------------+--------------+
              | If needs_regeneration and     |
              | retries < 2: feed feedback   |
              | back into Stage 2 only         |
              +------------------------------+
```

- **Stage 1**: `classifier` and `nlu_context` run together. NLU is prompted to cope with an empty `classifier_output` when both start at once; Stage 2 always sees the real classifier result merged into the payload.
- **Stage 2**: `response_generator` and `dashboard_state` run together on the merged context (plus optional regeneration feedback).
- **Stage 3**: `evaluator` then `orchestrator`; up to **2 regeneration attempts** re-run Stage 2 with combined orchestrator + evaluator feedback.
