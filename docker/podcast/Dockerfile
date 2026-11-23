# Container image for the podcast Lambda: Gradium Phonon (ptts) needs its model
# weights on disk, so they are downloaded once here and read offline at runtime.
# Only the Apache-2.0 phonon-31m-en weights are used (the 40M models are CC BY-NC).
# Build context is the repository root (see lib/rss_lambda_stack.ts).
FROM public.ecr.aws/lambda/python:3.13

COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --group podcast --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY src/rss_email ${LAMBDA_TASK_ROOT}/rss_email

# Runtime is offline, so every voice that PODCAST_VOICE may select must be fetched here.
# To use another catalogue voice, add it to this list and redeploy.
ARG PODCAST_VOICES="Hugh Toby Harper"
ENV HF_HOME=/opt/hf
RUN PYTHONPATH="${LAMBDA_TASK_ROOT}" python -c "import ptts; t = ptts.TTS(lang='en'); [t.synth('Warm up.', voice=v) for v in '${PODCAST_VOICES}'.split()]"
ENV HF_HUB_OFFLINE=1

CMD ["rss_email.generate_podcast.lambda_handler"]
