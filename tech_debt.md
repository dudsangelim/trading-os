# Trading OS — Tech Debt

## TD-001 — git_hash UNKNOWN no startup_version

**Data:** 2026-04-18  
**Severidade:** Baixa (cosmética, não afeta runtime)  
**Contexto:** Adicionado em commit `7b97e0e` (`chore(worker): log git hash and engine roles at startup`)

### Problema

O evento `startup_version` emitido no boot do worker loga `git_hash: "UNKNOWN (no git in container)"` porque `git` não está instalado na imagem `stack-trading-worker` (Dockerfile instala apenas `gcc libpq-dev curl`).

O propósito do campo — identificar qual versão do código está rodando — fica comprometido: não é possível confirmar pelo log se o container está desatualizado.

### Causa raiz

`Dockerfile.worker` não inclui `git`. Tentar instalar `git` só para `rev-parse` é overhead desnecessário de imagem.

### Solução recomendada

Passar o hash via `ARG` no build e expô-lo como `ENV`:

```dockerfile
ARG GIT_COMMIT=UNKNOWN
ENV GIT_COMMIT=${GIT_COMMIT}
```

No build:

```bash
docker compose build --build-arg GIT_COMMIT=$(git rev-parse HEAD) trading-worker
```

No `_log_startup_version()`, ler a env var no lugar do subprocess:

```python
git_hash = os.environ.get("GIT_COMMIT", "UNKNOWN (build-arg not set)")
```

### Impacto de não corrigir

Logs de startup continuam úteis (engine_roles estão corretos). Só o `git_hash` fica `UNKNOWN`. Aceitável até que haja um pipeline de CI/CD formal.

### Pré-requisito

Padronizar o processo de deploy em script ou Makefile que já passe `--build-arg GIT_COMMIT=$(git -C /home/agent/trading-os rev-parse HEAD)` automaticamente (ver também a questão de sincronização `agents/stack/trading/` vs `trading-os/trading/`).

---

## TD-002 — Consolidação de deploy point (NÃO RESOLVIDO, guardrail ativado 2026-04-18)

Contexto descoberto em 2026-04-18:
- `agents/stack/docker-compose.yml`: deploy efetivo dos containers trading (worker, overlay, api) + Jarvis OS legado
- `trading-os/docker-compose.yml`: deploy apenas do carry_worker
- `agents/stack/trading/` é cópia idêntica de `trading-os/trading/`, usada como build context
- Redes isoladas (`jarvis_net` vs `trading_net`), postgres duplicados (jarvis_postgres legado + trading_postgres isolado), secrets hardcoded no compose do `agents/stack/`

Por que não foi resolvido:
- Consolidação requer mapear dependências do Jarvis OS legado (laudos-service, edgelab, telegram-bot) que possivelmente consomem dados do trading via jarvis_postgres
- Risco não-mapeado de quebrar integrações invisíveis
- Escopo estimado: 4-8h de trabalho dedicado

Guardrail ativado:
- Script `check_deploy_sync.sh` roda a cada 6h via cron
- Alerta se `agents/stack/trading/` divergir de `trading-os/trading/`
- Previne recorrência de dessincronização silenciosa

Plano para consolidação futura (ordem):
1. Mapear dependências do Jarvis OS legado no jarvis_postgres
2. Decidir se trading migra para trading_postgres ou continua em jarvis_postgres
3. Decidir política de rede (consolidar ou manter isolamento)
4. Extrair secrets para .env não-versionado
5. Migrar definição dos 3 containers trading para `trading-os/docker-compose.yml`
6. Testar em janela de baixo tráfego
7. Deletar `agents/stack/trading/` após 7 dias de operação estável

---

## TD-003 — Ghost trades mistos pré/pós-rebuild (RESOLVIDO apenas pela documentação)

Em 2026-04-18 rebuild, o overlay_worker migrou de evaluator single-variant para multi-variant. Implicação:
- Ghost trades gravados antes de 2026-04-18 16:30 UTC têm `variant_label=NULL` (equivalente a baseline no modelo antigo)
- Ghost trades gravados depois têm `variant_label` explícito (baseline, zones_nearest, zones_weighted)
- Queries agregando ghost trades devem filtrar por `variant_label` ou por período para não misturar épocas

Status: documentado. Sem ação de migração (dados antigos preservados as-is).
