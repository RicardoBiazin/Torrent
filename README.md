# Torrent Downloader

Cliente BitTorrent leve em Python, com interface gráfica (Tkinter) e linha de comando. Usa [libtorrent](https://www.libtorrent.org/) para a rede e o protocolo.

- Magnet links e arquivos `.torrent`
- Vários downloads ao mesmo tempo, com pausar, retomar e remover (com ou sem apagar os arquivos)
- Retomada entre sessões, sem verificar tudo de novo
- Download sequencial
- Streaming: reproduz vídeo ou áudio enquanto ainda está baixando
- Agendamento de horários para parar downloads e/ou uploads
- Mapa de peças e lista de peers (cliente, velocidade, progresso, criptografia, país)
- Limites de download e upload; upload limitado a 100 KB/s por padrão
- Criptografia do protocolo BitTorrent habilitada; DHT, LSD, UPnP e NAT-PMP ligados

## Instalação

Requer Python 3.10 ou mais novo.

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Usa libtorrent 2.1.1.

## Uso

Interface gráfica:

```
python torrent_gui.py
```

Linha de comando (um torrent por vez):

```
python torrent_downloader.py "magnet:?xt=urn:btih:..."
python torrent_downloader.py arquivo.torrent -o D:\Downloads --max-down 5000 --max-up 500 --sequential
python torrent_downloader.py --version
```

Ctrl+C interrompe a linha de comando.

## Streaming

Selecione o torrent e clique em **Reproduzir**. Se houver mais de um arquivo de vídeo ou áudio, escolha qual tocar.

O programa sobe um servidor local em `http://127.0.0.1:<porta>` e abre o arquivo no player. O player pede o trecho que quer tocar, e o programa baixa primeiro as peças desse trecho. Pular para outro ponto do vídeo também funciona: as peças do ponto novo passam na frente.

Ao reproduzir, o torrent passa a baixar em ordem, e o início e o fim do arquivo vêm primeiro. Muitos formatos guardam o índice no final, e o player lê essa parte antes de começar.

Player usado, nesta ordem:

1. o definido em **Configurações > Player para streaming**
2. VLC ou mpv instalados
3. o navegador padrão, que toca MP4, WebM, MP3, M4A, OGG, FLAC e WAV, mas não MKV nem AVI

O servidor só aceita conexões do próprio computador.

## Agendamento

Em **Agendamento**, crie regras com os dias da semana, o horário de início e fim e o que parar.

| Download | Upload | O que acontece |
|---|---|---|
| Parado | Parado | a sessão inteira pausa |
| Parado | Liberado | os torrents param de baixar e continuam semeando |
| Liberado | Parado | upload limitado a 1 byte/s |

- Fim antes do início (por exemplo, 23:00 às 06:00) atravessa a meia-noite. O dia marcado é o do início.
- Início igual ao fim vale o dia inteiro.
- Com duas regras ativas ao mesmo tempo, vale a mais restritiva.
- O agendamento não mexe no que você pausou à mão: quando a regra termina, cada torrent volta ao estado em que estava.
- Upload parado deixa o download lento, porque os outros peers dão prioridade a quem também envia.

O agendamento e o streaming existem só na interface gráfica. A linha de comando não tem esses recursos.

## Executável para Windows

```
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name TorrentDownloader --collect-all libtorrent --specpath build --workpath build --distpath dist torrent_gui.py
```

O resultado é `dist\TorrentDownloader.exe`. O executável guarda o estado em `.state\` e os downloads em `downloads\`, ao lado dele.

## Onde ficam os dados

| Caminho | Conteúdo |
|---|---|
| `.state/settings.json` | configurações |
| `.state/jobs.json` | lista de torrents para retomar |
| `.state/<hash>.fastresume` | dados de retomada de cada torrent |
| `.state/geoip_cache.json` | cache de país por IP |
| `downloads/` | pasta de destino padrão |

Nenhum desses arquivos vai para o repositório (veja `.gitignore`).

## Privacidade

Para mostrar o país dos peers, a interface gráfica envia os IPs públicos dos peers conectados ao serviço gratuito [ip-api.com](https://ip-api.com/). O plano gratuito só aceita HTTP sem criptografia. Os resultados ficam em cache local. A linha de comando não faz essa consulta.

Torrent expõe seu IP aos outros peers. A criptografia do protocolo dificulta a identificação do tráfego pelo provedor, mas não esconde seu endereço.

## Limitações conhecidas

- Só a porta mínima (`port_min`) é usada para escutar; `port_max` fica salva mas não tem efeito.
- Mudar a porta exige reiniciar o programa.

## Testes

```
pip install pytest
python -m pytest tests
```

Os testes criam um torrent local e semeiam em `127.0.0.1`, sem DHT nem acesso à internet.

## Uso responsável

Use apenas para baixar e compartilhar conteúdo que você tem direito de distribuir, como distribuições Linux, software livre e obras em domínio público ou com licença aberta.

## Licença

[MIT](LICENSE)
