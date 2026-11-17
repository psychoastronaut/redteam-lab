# VPN da TryHackMe

1. Na sua conta THM: Access > OpenVPN Config, baixe o arquivo `.ovpn`.
2. Coloque ele aqui dentro, como `vpn/thm.ovpn`.
3. Conecte **de dentro do container**, nao do host:

   docker exec -it redteam-kali openvpn --config /vpn/thm.ovpn --daemon

4. Confirme que pegou IP na faixa 10.10.x.x ou 10.50.x.x:

   docker exec redteam-kali ip addr show tun0

5. So depois disso suba uma maquina no THM e rode o agente com o IP dela.

Nao versione o `.ovpn` nem credenciais em lugar nenhum -- esse diretorio
ja fica fora de qualquer commit (ver .gitignore).
