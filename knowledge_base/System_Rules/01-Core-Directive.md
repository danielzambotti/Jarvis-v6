# Core Directive: Jarvis v6.0 Knowledge Vault

This vault is the **Single Source of Truth** for Jarvis v6.0.

All architectural decisions, system rules, and active context must be recorded here before being considered canonical. No decision exists unless it is written in this vault.

## Vault Structure

| Directory | Purpose |
|---|---|
| `Architecture_Decisions/` | ADRs — every major stack or design choice |
| `System_Rules/` | Immutable operating principles for Jarvis |
| `Active_Context/` | Live working context, current sprint, open questions |

## Governing Principles

1. **Write first, act second.** Document the decision before implementing it.
2. **Markdown only.** All files use `.md` with consistent headers.
3. **Immutable history.** ADRs are never deleted — superseded ones are marked `[SUPERSEDED]`.
4. **RAG-ready.** Every file is written to be indexable by NotebookLM or any vector store.

---

## Map of Content — System Architecture (MOC)

This file is the entry node for the Jarvis v6.0 knowledge graph.
All architectural decisions radiate from here.

### Perimeter Control
- [[Security-Perimeter-Gitignore]] — `.gitignore` blocking secrets, keys, and runtime artefacts from VCS

### Security Architecture Chain
- [[ADR-002-ZeroTrust-Sandbox]] — Ephemeral Docker execution isolation
- [[ADR-003-IAM-Secrets-Vault]] — RBAC, JWT, and secrets abstraction
- [[ADR-004-DLP-Data-Protection]] — PII/secret redaction (GDPR/LGPD/PCI-DSS)
- [[ADR-005-AI-Security-Guardrails]] — LLM prompt injection prevention
- [[ADR-006-Conversational-Memory-RAG]] — Postgres/Redis memory + context injection

### GitOps & Deployment
- [[ADR-007-Autonomous-GitOps]] — GitOps bridge, CI pipeline, autonomous PR lifecycle

### Observability
- [[ADR-008-Observability-Stack]] — Prometheus + Grafana metrics, 4 core counters/histograms, auto-provisioned dashboard

### Document Security
- [[ADR-009-Document-Security]] — Secure file ingestion: MIME validation, AV scan hook, PDF metadata stripping, safe text extraction

### Active Context
- [[Teste_NotebookLM]] — Phase 1 ignition test results
