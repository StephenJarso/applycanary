# Deploy ApplyCanary on AWS

This Terraform stack runs the agent on **Amazon ECS (Fargate)** behind an
**Application Load Balancer**, and gives it AWS identities for the services it
uses:

| AWS service | What the agent does with it |
|---|---|
| **Amazon Bedrock** | Foundation-model inference (Claude via Converse) + Titan embeddings — the LLM and vector provider |
| **Amazon Polly** | Neural text-to-speech for the spoken interviewer |
| **Amazon Transcribe** | Streaming speech-to-text for spoken answers |
| **Amazon S3** | Interview audio recordings (versioned, private bucket) |
| **Amazon ECS / Fargate** | Containerized agent workload (web + scheduler in one task) |
| **CloudWatch** | Container logs + metrics, ALB access |

The **memory layer is CockroachDB Serverless**, provisioned with ccloud
(`scripts/ccloud/provision.sh`) and reached over the Postgres wire protocol —
the app's transactional state, embeddings, and agent memory all live there.

## Prerequisites

- Terraform ≥ 1.5 and the AWS CLI (`aws configure`)
- A CockroachDB Serverless cluster: `./scripts/ccloud/provision.sh`
- The container image built and pushed to ECR or a registry you can pull

## Steps

```bash
# 1. Build the image (from repo root)
docker build -t applycanary:latest .

# 2. Push to ECR
aws ecr create-repository --repository-name applycanary
aws ecr get-login-password | docker login --username AWS --password-stdin \
  <account>.dkr.ecr.us-east-1.amazonaws.com
docker tag applycanary:latest <account>.dkr.ecr.us-east-1.amazonaws.com/applycanary:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/applycanary:latest

# 3. Configure the stack
cp terraform.tfvars.example terraform.tfvars   # edit image, db_url, secret_key
terraform init
terraform apply -var-file=terraform.tfvars

# 4. Open the printed app_url
```

## Environment/secrets

Secrets live in **SSM Parameter Store (SecureString)** — the task definition
references them via `secrets`, so they never appear in plaintext in ECS or git:

- `/applycanary/DATABASE_URL` — CockroachDB connection string
- `/applycanary/SECRET_KEY` — session-signing secret
- `/applycanary/LLM_API_KEY` — optional Gemini/OpenRouter key (Bedrock is IAM-only)

Additional config (SMTP, GitHub, toggles) goes in `extra_env` in tfvars.

## Security posture

- **Least privilege IAM**: the task role only grants Bedrock invoke, Polly
  synthesize, Transcribe stream, and S3 on its own bucket.
- **S3 bucket is private** and versioned; audio is never world-readable.
- **Secrets never in git or the image** — SSM SecureString only.
- **Health checks** hit `/health`, which reports scheduler state; a hung
  scheduler fails the check and ECS replaces the task.
- **HTTPS** via an ACM cert when `acm_certificate_arn` is set; plain HTTP
  otherwise (demo only — put auth in front of it).
- **Auditable agent access**: every MCP/CLI touch of CockroachDB is logged
  (see `mcp/README.md` and `scripts/ccloud/audit.sh`).

## Operations

```bash
terraform output app_url          # your URL
./scripts/ccloud/status.sh        # memory-layer health
./scripts/ccloud/backup.sh        # on-demand backup before risky changes
aws ecs update-service --cluster applycanary --service applycanary \
  --force-new-deployment          # redeploy after an image push
```
