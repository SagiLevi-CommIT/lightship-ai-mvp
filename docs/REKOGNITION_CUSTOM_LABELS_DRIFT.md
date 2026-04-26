# Rekognition Custom Labels — manual AWS drift & codification

This document lists **manual** AWS changes applied during the Custom Labels
integration, the **exact CLI patterns** used, and where the desired state is
already reflected in **CloudFormation** (or intentionally left manual).

## Codified in templates (no further action once CFN deploy role is fixed)

| Area | Template | Notes |
|------|----------|-------|
| Lambda env `REKOGNITION_CUSTOM_MODEL_ARN` from SSM | `infrastructure/backend-lambda-stack.yaml`, `infrastructure/app-stack.yaml` | `!Sub '{{resolve:ssm:/${ProjectName}/${Environment}/rekognition/custom-labels-arn}}'` |
| IAM: `DetectCustomLabels`, `DescribeProjectVersions`, `DescribeProjects`, `StartProjectVersion`, `StopProjectVersion` | `infrastructure/app-stack.yaml` (`RekognitionAccess` inline policy on Lambda role) | Matches manually widened inline policy on `lightship-mvp-lambda-role`. |

**CFN deploy is blocked** (per DevOps): `lightship-mvp-cfn-app-deploy-role` needs
`elasticloadbalancing:DescribeLoadBalancers` (and any other missing bits) before
stack updates can re-import the above.

## SSM parameter (currently manual resource)

**Parameter:** `/lightship/mvp/rekognition/custom-labels-arn`  
**Value (trained model):** ProjectVersionArn for version **`v2domain`** on project
`lightship-mvp-objects`, e.g.:

`arn:aws:rekognition:us-east-1:336090301206:project/lightship-mvp-objects/version/v2domain/1777231723938`

```bash
aws ssm put-parameter \
  --profile lightship --region us-east-1 \
  --name /lightship/mvp/rekognition/custom-labels-arn \
  --type String \
  --value "<ProjectVersionArn>" \
  --overwrite
```

**Future CFN:** add `AWS::SSM::Parameter` when the stack owns this name; until
then the Lambda `{{resolve:ssm:...}}` reference assumes the parameter exists.

## Lambda environment (manual until CFN redeploy)

Lambda reads `REKOGNITION_CUSTOM_MODEL_ARN` at cold start. Until CloudFormation
updates the function, merge **all** existing `Environment.Variables` and set:

```bash
# Snapshot current config, merge REKOGNITION_CUSTOM_MODEL_ARN in a JSON file:
aws lambda get-function-configuration \
  --profile lightship --region us-east-1 \
  --function-name lightship-mvp-backend \
  --output json > /tmp/lambda.json

# Edit Variables → REKOGNITION_CUSTOM_MODEL_ARN = <ProjectVersionArn from SSM>

aws lambda update-function-configuration \
  --profile lightship --region us-east-1 \
  --function-name lightship-mvp-backend \
  --environment file:///tmp/lambda_env_update.json
```

**Applied in this effort:** `REKOGNITION_CUSTOM_MODEL_ARN` was set to the
`v2domain` ProjectVersionArn (was previously `""`).

## Lambda container image (ECR)

**Current image URI (example):**
`336090301206.dkr.ecr.us-east-1.amazonaws.com/lightship-backend:latest`

**Local build & push** (requires Docker Desktop / daemon running):

```bash
cd lambda-be
export AWS_PROFILE=lightship
export AWS_REGION=us-east-1
export ACCOUNT=336090301206
export REPO=lightship-backend
export TAG=$(git rev-parse --short HEAD)

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin \
  "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build -t "$REPO:$TAG" .
docker tag "$REPO:$TAG" "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:$TAG"
docker tag "$REPO:$TAG" "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest"
docker push "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:$TAG"
docker push "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest"

aws lambda update-function-code \
  --profile lightship --region us-east-1 \
  --function-name lightship-mvp-backend \
  --image-uri "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:$TAG"
```

**Agent host note:** On 2026-04-26 the integration machine had **no Docker
daemon** (`npipe:////./pipe/dockerDesktopLinuxEngine` missing). Image build/push
was **not** executed from automation; the ECR `latest` tag may still point at an
image **without** `rekognition_labeler.py` custom-label audit fields. After you
push a new image, re-run `scripts/validate_custom_labels_e2e.py`.

## Rekognition training & data layout

- **Project:** `lightship-mvp-objects`
- **Curated training:** Domain-focused label set with **≥10 images per class**
  (Rekognition minimum). A broader 16-label manifest failed validation until
  per-class counts were enforced.
- **Augmented manifest:** SageMaker-style JSON lines with `bounding-box`
  metadata; manifests must use **real newline** separators (not the two-character
  sequence `\\n`).

**S3 layout (important):** Rekognition training output must go under a prefix
allowed by the processing bucket policy for Rekognition `PutObject`, e.g.:

`s3://lightship-mvp-processing-336090301206/rekognition-finetune/training-output/<run-id>/`

### Training API quirk (documented in `scripts/train_custom_labels.py`)

For some projects, `CreateProjectVersion` with inline `TrainingData` rejects a
manifest that **`CreateDataset` accepts**. Reliable sequence:

1. `aws rekognition create-dataset` (TRAIN) ← `train_split.manifest`
2. `aws rekognition create-dataset` (TEST) ← `test_split.manifest`
3. `create_project_version` with **only** `ProjectArn`, `VersionName`,
   `OutputConfig` (omit `TrainingData` / `TestingData`).

### Destructive / experimental actions (audit trail)

- **`DeleteProjectVersion`** was used on an older trained version (**v1**) on
  `lightship-mvp-objects` during manifest debugging; that version is gone.
  Current production-trained version name: **`v2domain`**.
- **Experimental projects** may exist from debugging (e.g. clones / test
  projects). List with `aws rekognition describe-projects` and delete unused
  projects in the console if desired.

## End-to-end validation

After **(1)** new Lambda image with custom labels code, **(2)** endpoint
`RUNNING`, **(3)** non-empty `REKOGNITION_CUSTOM_MODEL_ARN`:

```bash
py scripts/validate_custom_labels_e2e.py \
  --project-version-arn "<ProjectVersionArn>" \
  --video-path build/validation_samsara_2.mp4 \
  --profile lightship
```

**2026-04-26 run:** Endpoint reached `RUNNING`, Lambda returned `output.json`, but
`rekognition_audit.per_frame` entries **lacked** `custom_labels_invoked` entirely
→ confirms **stale Lambda image**. After redeploying the image, assertions should
pass (`custom_labels_invoked == true`, at least one non-empty `custom_raw_labels`).

## IAM drift (already aligned in `app-stack.yaml`)

Manually added to role `lightship-mvp-lambda-role` inline policy `RekognitionAccess`:

- `rekognition:DetectCustomLabels`
- `rekognition:DescribeProjectVersions`

Extended in templates for future Step Functions orchestration:

- `rekognition:DescribeProjects`
- `rekognition:StartProjectVersion`
- `rekognition:StopProjectVersion`

## Matching CFN snippets (reference)

Lambda environment (image stack):

```yaml
REKOGNITION_CUSTOM_MODEL_ARN: !Sub '{{resolve:ssm:/${ProjectName}/${Environment}/rekognition/custom-labels-arn}}'
```

Rekognition policy actions (abbreviated):

```yaml
- rekognition:DetectLabels
- rekognition:DetectCustomLabels
- rekognition:DescribeProjectVersions
- rekognition:DescribeProjects
- rekognition:StartProjectVersion
- rekognition:StopProjectVersion
```
