# Changelog

## 1.1.1 - 2026-10-24

### Alterado
- libtorrent 2.0.13 para 2.1.1. O executável agora é gerado com Python 3.13.17 e PyInstaller 6.22.3.
- APIs que ficaram obsoletas no libtorrent 2.1 foram trocadas: `torrent_info.files()` por `layout()`, `add_torrent(dict)` por `add_torrent_params`.

### Corrigido
- Streaming no libtorrent 2.1: o trecho entregue ao player podia vir com zeros. A peça já constava como baixada enquanto os dados ainda estavam no cache de escrita, e a leitura direta do disco pegava o arquivo vazio. Agora a peça é lida pelo próprio libtorrent (`read_piece`).
- A interface tira os alertas da fila a cada segundo; antes a fila só crescia.

## 1.1.0 - 2026-10-21

### Adicionado
- Streaming: botão **Reproduzir** toca vídeo ou áudio enquanto baixa. Servidor HTTP local (só `127.0.0.1`) com suporte a Range; as peças do trecho pedido pelo player são baixadas primeiro (`set_piece_deadline`). Abre no player configurado, no VLC ou mpv, ou no navegador.
- Agendamento: regras por dia da semana e horário para parar downloads, uploads ou os dois. Aceita regra que atravessa a meia-noite e regra de dia inteiro. Não altera o que foi pausado à mão.
- Configuração do player usado no streaming.
- Testes do agendamento e do streaming (seed local com velocidade limitada).

### Corrigido
- A lista de arquivos ignora os arquivos de preenchimento (pad) de torrents v2 e híbridos.

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
