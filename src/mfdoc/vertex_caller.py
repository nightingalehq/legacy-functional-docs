"""Claude-via-Vertex-AI ModelCaller for `mfdoc batch`.

Isolated the same way anthropic_caller.py is: the `anthropic` package's
Vertex extra (which pulls in `google-auth`) stays optional, so nothing
outside `mfdoc batch --provider vertex` ever needs it installed.

This backs Claude models running on Vertex AI (`anthropic.AnthropicVertex`),
not Google's own Gemini models -- same model family our writing-rules
prompting and citation-discipline behaviour are built against, just a
different egress path (Google Cloud, not Anthropic direct). See issue #12
and docs/guides/security-and-compliance.md's data-flow section for why that
distinction matters to a client's compliance posture. Routing Gemini or any
other non-Claude model through this caller is out of scope until the Phase
5.1 eval prompts have been run against it specifically -- see issue #12.
"""

from __future__ import annotations

import os

from .batch import ModelResponse

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_REGION = "us-east5"


class VertexCaller:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS,
                 project: str | None = None, region: str | None = None):
        try:
            from anthropic import AnthropicVertex
        except ImportError as exc:
            raise RuntimeError(
                "`mfdoc batch --provider vertex` needs the `anthropic` package's Vertex "
                "extra: pip install 'mfdoc[vertex]'"
            ) from exc

        project = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
        region = region or os.environ.get("ANTHROPIC_VERTEX_REGION") or DEFAULT_REGION
        if not project:
            raise RuntimeError(
                "no GCP project configured -- set options.narrative.vertex.project in "
                "project.yml, pass --gcp-project, or set GOOGLE_CLOUD_PROJECT"
            )

        # Credentials come from the ambient environment (ADC: `gcloud auth
        # application-default login`, a service account, or workload identity)
        # the same way any other Vertex client picks them up -- this caller
        # never handles a key file itself.
        self._client = AnthropicVertex(project_id=project, region=region)
        self.model = model
        self.max_tokens = max_tokens

    def __call__(self, prompt: str) -> ModelResponse:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return ModelResponse(
            text=text,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
