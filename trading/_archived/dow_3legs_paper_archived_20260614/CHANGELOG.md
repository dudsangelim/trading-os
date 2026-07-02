## 2026-04-29 — Leverage 1.5 → 1.0 (auditoria v1.1.1)
- Motivo: auditoria classificou lev=1.5 como Classe C (DD bootstrap
  IC95 high 62%); recuo para Classe B durante validação inicial.
- Plano: manter lev=1.0 por 6 semanas. Subir para 1.5x apenas se
  slippage real ≤ 5 bps por perna e PF rolling se mantém > 1.40.
- Auditoria completa: backtests/dow_3leg_audit_v111/ no repo local.
