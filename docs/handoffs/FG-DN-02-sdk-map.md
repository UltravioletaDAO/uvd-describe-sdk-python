# FG-DN-02 — el `sdk-map` verificado

## Estado

- **Hecho:** `schema/openapi.json` + `schema/sdk.overlay.yaml` vendoreados de
  `UltravioletaDAO/describe-net@434e01c6f2826405d9dadcb473d71a8398371d3b`, con repo, commit y
  `sha256` en `schema/SOURCE`. `tests/test_sdk_map.py` conduce cada método público de
  `DescribeClient` y `DescribeNames` contra un `httpx.MockTransport` que graba
  `(verbo, host, ruta, claves de query)`, lo resuelve a `operationId` contra el spec con el overlay
  aplicado y compara con `sdk-map.json` (commiteado). Mutaciones en rojo en el mismo archivo.
- **Falta:** nada del encargo. La PR `[FG-DN-02]` queda abierta para la refutación de c0der.
- **Próximo paso:** cuando describe-net cambie el spec o el overlay, copiar los dos archivos del
  commit nuevo, actualizar `schema/SOURCE` y regenerar el mapa:
  `SDK_MAP_ESCRIBIR=1 python -m pytest tests/test_sdk_map.py -q`.

## Lo medido

- Spec: 33 operaciones, 66 schemas. Overlay: 11 con `x-fern-ignore: true`, 22 públicas.
- `mapeadas` (8): `getAgentReputation`→`agent`, `getHealth`→`health`, `getLeaderboard`→`leaderboard`,
  `getWalletChains`→`wallet`, `getWalletHistory`→`wallet_history`,
  `getWalletReputation`→`wallet_breakdown`, `resolveName`→`names.resolve`,
  `reverseName`→`names.reverse`.
- `fuera` (14): `getWalletBadge` (`badge_url()` arma la URL y no llama a la red) y 13 operaciones
  sin método en el SDK Python.
- Métodos pagos (`wallet_breakdown`, `wallet_history`, `agent`): el grabador sí llega. Se conducen
  con un 200 al primer intento (la rama «el servicio no cobró»), que pega en la misma ruta que el
  402; ningún test firma. Ninguno quedó en `fuera` por 402.
- Sin PyYAML (no es dependencia): el overlay se lee con un lector de gramática cerrada, y cada
  `target` se valida contra el spec (un target huérfano es rojo).

## Refutaciones al encargo

- «igual al spec vivo»: no es igual byte a byte. Operaciones, parámetros y schemas coinciden; el
  vivo difiere en textos de `description` y en `info.x-rate-limit-policy`. Se vendoreó `434e01c6`
  tal cual pide el encargo.

## Mutaciones (qué test cae)

| Mutación | Test en rojo |
|---|---|
| operación inventada en una copia del spec (en el archivo) | `test_mutacion_una_operacion_inventada_es_roja` |
| operación inventada en `schema/openapi.json` | `test_cada_archivo_vendoreado_tiene_el_sha256_de_source`, `test_ninguna_operacion_publica_queda_sin_clasificar`, `test_el_overlay_clasifica_cada_operacion` |
| el SDK llama a una ruta fuera del spec (`/v1/names/resolv`) | `test_cada_llamada_resuelve_a_una_operacion_publica` (y los que usan el mapa) |
| método público nuevo sin conducir | `test_cada_metodo_publico_se_conduce_o_se_declara_sin_red` |
