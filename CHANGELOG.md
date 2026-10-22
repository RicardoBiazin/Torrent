# Changelog

## 1.0.0 - 2026-10-21

Primeira versão pública.

### Corrigido
- Retomada entre sessões nunca funcionou: o `.fastresume` era gravado com `lt.bencode()` sobre um `add_torrent_params` e saía com 2 bytes. Agora usa `lt.write_resume_data_buf()` e salva os metadados (`save_info_dict`), então o torrent reabre sem reverificar e sem buscar metadados de novo.
- "Remover apenas da lista" quebrava com `ArgumentError` (`lt.options_t(0)` não é aceito pelo binding).
- No executável, estado e downloads iam para a pasta temporária do PyInstaller e se perdiam ao fechar. Agora ficam ao lado do `.exe`.

### Adicionado
- Versão no título da janela e `--version` na linha de comando.
- Testes automatizados sem rede (`tests/`).
- README, licença MIT e instruções para gerar o executável.

### Alterado
- A linha de comando reaproveita os formatadores da engine.
- Troca de `set_sequential_download()` (obsoleto) por `set_flags`/`unset_flags`.
- Removido o arquivo vazio `torrent.app`.
