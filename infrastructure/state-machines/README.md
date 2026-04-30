# Step Functions ASL

The **authoritative** pipeline definition is inlined in
`infrastructure/backend-lambda-stack.yaml` under `LightshipPipelineStateMachine`
(`DefinitionString` / `DefinitionSubstitutions`).

`pipeline.asl.json` in this folder is a **legacy Lambda-only** sketch and does
not match production (which uses **ECS `runTask.sync`** + Lambda fallback).

After deploy, you can export the live definition with:

```bash
aws stepfunctions describe-state-machine \
  --state-machine-arn arn:aws:states:us-east-1:336090301206:stateMachine:lightship-mvp-pipeline \
  --query definition --output text > build/sfn-exported.json
```

The SQS payload includes `ecs_env` (string fields for ECS overrides); see
`lambda-be/src/api_server.py` (`_enqueue_job`) and `build/patch_sfn_ecs_env.py`.
