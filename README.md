# Torrent Downloader

Cliente BitTorrent leve em Python, com interface gráfica (Tkinter) e linha de comando. Usa [libtorrent](https://www.libtorrent.org/) para a rede e o protocolo.

- Magnet links e arquivos `.torrent`
- Vários downloads ao mesmo tempo, com pausar, retomar e remover (com ou sem apagar os arquivos)
- Retomada entre sessões, sem verificar tudo de novo
- Download sequencial, para assistir enquanto baixa
- Mapa de peças e lista de peers (cliente, velocidade, progresso, criptografia, país)
- Limites de download e upload; upload limitado a 100 KB/s por padrão
- Criptografia do protocolo BitTorrent habilitada; DHT, LSD, UPnP e NAT-PMP ligados

## Instalação

Requer Python 3.10 ou mais novo.

```
pip install -r requirements.txt
```

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
