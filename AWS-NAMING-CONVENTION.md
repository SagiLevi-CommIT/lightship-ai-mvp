# AWS Resources Naming Convention

This document defines the standard naming convention for AWS resources to ensure consistent identification and clear context across all AWS accounts.

## General Principles

- **All AWS resources that support naming MUST be given a name tag**
- **Consistent patterns** enable clear identification and context understanding
- **Apply to all resource types**, even if not explicitly listed below

## Naming Structure

### Multi-VPC Solutions
```
<object type>-<shorten vpc name>-<object name>[-<object version>]
```

**Examples:**
- `i-mgmt-bastion-linux-v1`
- `i-stg-k8s-asg-worker`
- `i-dev-app-server-01`
- `net-dev-private-az1`

### Single VPC Solutions
```
<object type>-<object name>[-<object version>]
```

**Examples:**
- `i-bastion-linux-v1`
- `i-sophos-utm`
- `i-alienvault-sensor`

## Naming Syntax Rules

### Format Requirements
- **All lowercase letters**
- **No spaces allowed**
- **Use hyphens (`-`) as word separators**
- **Consistent object type prefixes**

### Version Control
- **Object version format**: `v<##>`
- **Use two-digit versioning**: `v01`, `v02`, `v03`
- **Prohibited words**: `new`, `old`, `previous`, `current` - use version numbers instead

### Special Cases
- **Temporary objects**: Add `_temp` suffix to names
- **Required tags**: 
  - `Description` - Explain the object's purpose
  - `Created-by` - Identify who created the resource

## Object Type Prefixes

### Compute & Instances
| Prefix | Resource Type |
|--------|---------------|
| `i` | EC2 Instance |
| `lt` | Launch Template |
| `asg` | Auto-Scaling Group |
| `sir` | Spot Instances Request |

### Networking
| Prefix | Resource Type |
|--------|---------------|
| `vpc` | VPC |
| `net` | Subnet |
| `netgr` | Subnet Group |
| `sgr` | Security Group |
| `rtb` | Routing Table |
| `rtb-tgw` | Transit Gateway Routing Table |
| `igw` | Internet Gateway |
| `vpg` | Virtual Private Gateway |
| `pcx` | Peering Connection |
| `nat` | NAT Gateway |
| `acl` | Network ACL |
| `dopt` | DHCP Options Set |
| `vpce` | VPC Endpoint |
| `eip` | Elastic IP |
| `cvpn` | Client VPN Endpoint |
| `mpl` | Managed Prefix List |

### Load Balancers & Targets
| Prefix | Resource Type |
|--------|---------------|
| `alb` | Application Load Balancer |
| `nlb` | Network Load Balancer |
| `elb` | Classic Elastic Load Balancer |
| `tg` | Target Group |

### Security & Access
| Prefix | Resource Type |
|--------|---------------|
| `policy` | IAM Policy |
| `role` | IAM Role |
| `ugr` | IAM User Group |
| `key-pair` | Key Pair |
| `cmk` | Customer Managed Key (KMS) |

### Storage & Database
| Prefix | Resource Type |
|--------|---------------|
| `rds` | RDS Instance |
| `snap` | RDS Snapshot |
| `pg` | Parameter Group |
| `ec` | ElastiCache |
| `docdb` | DocumentDB (MongoDB) |
| `dax` | DynamoDB DAX |
| `mdb` | Memory DB Cluster |

### Containers & Orchestration
| Prefix | Resource Type |
|--------|---------------|
| `dkr` | Docker Container |
| `taskdef` | ECS Task Definition |
| `svc` | ECS Service |
| `ecs` | ECS Cluster |
| `ecr` | ECR Repository |
| `eks` | EKS Cluster |
| `ng` | EKS Managed Node Group |

### Serverless & Functions
| Prefix | Resource Type |
|--------|---------------|
| `lambda` | Lambda Function |

### CI/CD & DevOps
| Prefix | Resource Type |
|--------|---------------|
| `repo` | CodeCommit Repository |
| `cb` | CodeBuild |
| `cf` | CloudFormation Stack |
| `cft` | CloudFormation Template |

### Monitoring & Logging
| Prefix | Resource Type |
|--------|---------------|
| `trail` | CloudTrail |
| `lgr` | CloudWatch Logs Group |
| `hc` | Route53 Health Check |
| `ebr` | EventBridge Rule |

### DNS & Networking Services
| Prefix | Resource Type |
|--------|---------------|
| `rslvr-in` | Route53 Resolver Inbound Endpoint |
| `rslvr-out` | Route53 Resolver Outbound Endpoint |

### Application Services
| Prefix | Resource Type |
|--------|---------------|
| `apigw` | API Gateway |
| `sns` | SNS Topic |
| `mq` | Amazon MQ |
| `kdf` | Kinesis Data Firehose |
| `es` | ElasticSearch |

### Management & Governance
| Prefix | Resource Type |
|--------|---------------|
| `dlm-policy` | Data Lifecycle Manager Policy |
| `rule` | Lifecycle Rule |
| `pp` | Provisioned Product (Service Catalog) |
| `ps` | SSO Permission Set |
| `pmw` | SSM Patch Manager Maintenance Window |
| `pmwt` | Patch Manager Maintenance Window Target |
| `pmwtt` | Patch Manager Maintenance Window Task |
| `spar` | SSM Parameter Store Parameter |

### Security & Identity
| Prefix | Resource Type |
|--------|---------------|
| `cup` | Cognito User Pool |
| `apl` | Cognito User Pool App Client |

## Environment-Specific Examples

### Development Environment
```
vpc-dev
net-dev-private-az1
net-dev-public-az2
sgr-dev-web-servers
i-dev-app-server-v01
alb-dev-web-frontend
```

### Production Environment
```
vpc-prod
net-prod-private-az1
net-prod-database-az2
sgr-prod-db-access
rds-prod-primary-v02
lambda-prod-data-processor
```

### Multi-VPC Management Setup
```
vpc-mgmt
i-mgmt-bastion-linux-v1
vpce-mgmt-s3-gateway
pcx-mgmt-to-prod
nat-mgmt-az1
```

## Required Tags

In addition to the naming convention, all resources must include these tags:

| Tag Name | Purpose | Example |
|----------|---------|---------|
| `Name` | Resource identifier following naming convention | `i-dev-web-server-v01` |
| `Description` | Clear explanation of resource purpose | `Development web server for API backend` |
| `Created-by` | Identify resource creator | `john.doe@company.com` |
| `Environment` | Environment classification | `dev`, `stg`, `prod` |
| `Project` | Project or application name | `vpc-infrastructure` |

## Best Practices

1. **Consistency**: Always follow the naming pattern across all environments
2. **Clarity**: Names should be self-descriptive and meaningful
3. **Versioning**: Use version numbers for resource iterations
4. **Documentation**: Maintain clear descriptions in tags
5. **Review**: Regularly audit resource names for compliance

## References

Based on AWS best practices:
- [AWS Tagging Strategies](https://aws.amazon.com/answers/account-management/aws-tagging-strategies/)
- AWS Well-Architected Framework naming guidelines