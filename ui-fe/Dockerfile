# Uses the public ECR Node image to avoid Docker Hub unauthenticated
# pull rate limits inside CodeBuild (`429 Too Many Requests` otherwise).

# -- Stage 1: deps --
FROM public.ecr.aws/docker/library/node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --prefer-offline --no-audit --no-fund

# -- Stage 2: build --
FROM public.ecr.aws/docker/library/node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE=""
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
RUN npm run build

# -- Stage 3: runtime --
FROM public.ecr.aws/docker/library/node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
