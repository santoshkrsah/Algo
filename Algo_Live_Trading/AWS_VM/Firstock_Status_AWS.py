#!/usr/bin/env python3
"""
Firstock_Status_AWS.py - Firstock account status check in ONE file (made for the AWS VM).

Same job as `python Firstock_Utils.py`: log in, read NIFTY LTP, funds, open positions and today's orders,
and send one report to Telegram. READ-ONLY: it never places, modifies or cancels an order.

Needs only Python 3.8+ with its built-in modules (no pip install). Works on Linux and Windows.

    python3 Firstock_Status_AWS.py                 # run the check (the first run asks for the credentials)
    python3 Firstock_Status_AWS.py --setup         # enter / change the credentials, then run the check
    python3 Firstock_Status_AWS.py --no-telegram   # run the check, print only, send nothing to Telegram

Files it keeps next to itself (same names as the main Firstock code, so it can sit in the same folder):
    Firstock_Credentials.txt   KEY = value lines (made by --setup, or copy the one from the PC)
    <USERID>_jkey.txt          session key, reused while it works (shared with strategies in the same folder)
    tokens_logs/               one log file per day (IST dates)
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
import socket
import ssl
import struct
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = "Firstock_Credentials.txt"
BASE_URL = "https://api.firstock.in/V1"
IST = timezone(timedelta(hours=5, minutes=30))

LOGIN_TRIES = 2                 # a status check must not hammer the broker with logins
VERIFY_TRIES, VERIFY_WAIT = 3, 5  # checks of a saved key before it is called dead (a single failed quote is normal)
MAX_ROWS = 15                   # rows per list in the Telegram report
QTY_KEYS = ("netQuantity", "netQty", "netqty", "quantity")
HIDDEN = ("token", "jkey", "userid", "actid", "request")  # never shown from the /limit reply

# (key in Firstock_Credentials.txt, question, hidden while typing, required)
FIELDS = [
    ("USERID", "Firstock user ID", False, True),
    ("PASSWORD", "Firstock password", True, True),
    ("API_KEY", "Firstock API key", True, True),
    ("API_SECRET", "Firstock API secret (vendor code)", True, True),
    ("TOTP_KEY", "TOTP secret key (the long code behind the authenticator QR)", True, True),
    ("STATIC_IP", "Static IP registered with Firstock", False, False),
    ("SOURCE_IP", "Private IP to send from (only if this VM has several IPs)", False, False),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token", True, False),
    ("TELEGRAM_GROUP_CHAT_ID", "Telegram chat id(s), comma separated", False, False),
]

C = {}               # credentials, filled by main()
SEND_TELEGRAM = True
log = logging.getLogger("firstock_status_aws")

# Public root certificates (copied from certifi / Mozilla's list), trusted IN ADDITION to the machine's own list.
# Windows only holds the roots it has needed so far, and a fresh AWS Windows VM has very few: Python then fails with
# "CERTIFICATE_VERIFY_FAILED ... self-signed certificate in certificate chain" (Telegram = Go Daddy Root G2,
# ipify = GTS Root R4 / GlobalSign). Verification stays fully on; these only fill the gaps.
EXTRA_ROOTS = """
# Go Daddy Root Certificate Authority - G2
-----BEGIN CERTIFICATE-----
MIIDxTCCAq2gAwIBAgIBADANBgkqhkiG9w0BAQsFADCBgzELMAkGA1UEBhMCVVMx
EDAOBgNVBAgTB0FyaXpvbmExEzARBgNVBAcTClNjb3R0c2RhbGUxGjAYBgNVBAoT
EUdvRGFkZHkuY29tLCBJbmMuMTEwLwYDVQQDEyhHbyBEYWRkeSBSb290IENlcnRp
ZmljYXRlIEF1dGhvcml0eSAtIEcyMB4XDTA5MDkwMTAwMDAwMFoXDTM3MTIzMTIz
NTk1OVowgYMxCzAJBgNVBAYTAlVTMRAwDgYDVQQIEwdBcml6b25hMRMwEQYDVQQH
EwpTY290dHNkYWxlMRowGAYDVQQKExFHb0RhZGR5LmNvbSwgSW5jLjExMC8GA1UE
AxMoR28gRGFkZHkgUm9vdCBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkgLSBHMjCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAL9xYgjx+lk09xvJGKP3gElY6SKD
E6bFIEMBO4Tx5oVJnyfq9oQbTqC023CYxzIBsQU+B07u9PpPL1kwIuerGVZr4oAH
/PMWdYA5UXvl+TW2dE6pjYIT5LY/qQOD+qK+ihVqf94Lw7YZFAXK6sOoBJQ7Rnwy
DfMAZiLIjWltNowRGLfTshxgtDj6AozO091GB94KPutdfMh8+7ArU6SSYmlRJQVh
GkSBjCypQ5Yj36w6gZoOKcUcqeldHraenjAKOc7xiID7S13MMuyFYkMlNAJWJwGR
tDtwKj9useiciAF9n9T521NtYJ2/LOdYq7hfRvzOxBsDPAnrSTFcaUaz4EcCAwEA
AaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYE
FDqahQcQZyi27/a9BUFuIMGU2g/eMA0GCSqGSIb3DQEBCwUAA4IBAQCZ21151fmX
WWcDYfF+OwYxdS2hII5PZYe096acvNjpL9DbWu7PdIxztDhC2gV7+AJ1uP2lsdeu
9tfeE8tTEH6KRtGX+rcuKxGrkLAngPnon1rpN5+r5N9ss4UXnT3ZJE95kTXWXwTr
gIOrmgIttRD02JDHBHNA7XIloKmf7J6raBKZV8aPEjoJpL1E/QYVN8Gb5DKj7Tjo
2GTzLH4U/ALqn83/B2gX2yKQOC16jdFU8WnjXzPKej17CuPKf1855eJ1usV2GDPO
LPAvTK33sefOT6jEm0pUBsV/fdUID+Ic/n4XuKxe9tQWskMJDE32p2u0mYRlynqI
4uJEvlz36hz1
-----END CERTIFICATE-----
# GTS Root R4
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvVYRg1rkDdc/eJkTBa6zzuhXyi
QHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHci8nHc8iMai/lxKvR
HYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D
9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8
p/SgguMh1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD
-----END CERTIFICATE-----
# GTS Root R1
-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlk28xsBNJiGuiFzANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjEwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjEwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQC2EQKLHuOhd5s73L+UPreVp0A8of2C+X0yBoJx9vaMf/vo
27xqLpeXo4xL+Sv2sfnOhB2x+cWX3u+58qPpvBKJXqeqUqv4IyfLpLGcY9vXmX7w
Cl7raKb0xlpHDU0QM+NOsROjyBhsS+z8CZDfnWQpJSMHobTSPS5g4M/SCYe7zUjw
TcLCeoiKu7rPWRnWr4+wB7CeMfGCwcDfLqZtbBkOtdh+JhpFAz2weaSUKK0Pfybl
qAj+lug8aJRT7oM6iCsVlgmy4HqMLnXWnOunVmSPlk9orj2XwoSPwLxAwAtcvfaH
szVsrBhQf4TgTM2S0yDpM7xSma8ytSmzJSq0SPly4cpk9+aCEI3oncKKiPo4Zor8
Y/kB+Xj9e1x3+naH+uzfsQ55lVe0vSbv1gHR6xYKu44LtcXFilWr06zqkUspzBmk
MiVOKvFlRNACzqrOSbTqn3yDsEB750Orp2yjj32JgfpMpf/VjsPOS+C12LOORc92
wO1AK/1TD7Cn1TsNsYqiA94xrcx36m97PtbfkSIS5r762DL8EGMUUXLeXdYWk70p
aDPvOmbsB4om3xPXV2V4J95eSRQAogB/mqghtqmxlbCluQ0WEdrHbEg8QOB+DVrN
VjzRlwW5y0vtOUucxD/SVRNuJLDWcfr0wbrM7Rv1/oFB2ACYPTrIrnqYNxgFlQID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQU5K8rJnEaK0gnhS9SZizv8IkTcT4wDQYJKoZIhvcNAQEMBQADggIBAJ+qQibb
C5u+/x6Wki4+omVKapi6Ist9wTrYggoGxval3sBOh2Z5ofmmWJyq+bXmYOfg6LEe
QkEzCzc9zolwFcq1JKjPa7XSQCGYzyI0zzvFIoTgxQ6KfF2I5DUkzps+GlQebtuy
h6f88/qBVRRiClmpIgUxPoLW7ttXNLwzldMXG+gnoot7TiYaelpkttGsN/H9oPM4
7HLwEXWdyzRSjeZ2axfG34arJ45JK3VmgRAhpuo+9K4l/3wV3s6MJT/KYnAK9y8J
ZgfIPxz88NtFMN9iiMG1D53Dn0reWVlHxYciNuaCp+0KueIHoI17eko8cdLiA6Ef
MgfdG+RCzgwARWGAtQsgWSl4vflVy2PFPEz0tv/bal8xa5meLMFrUKTX5hgUvYU/
Z6tGn6D/Qqc6f1zLXbBwHSs09dR2CQzreExZBfMzQsNhFRAbd03OIozUhfJFfbdT
6u9AWpQKXCBfTkBdYiJ23//OYb2MI3jSNwLgjt7RETeJ9r/tSQdirpLsQBqvFAnZ
0E6yove+7u7Y/9waLd64NnHi/Hm3lCXRSHNboTXns5lndcEZOitHTtNCjv0xyBZm
2tIMPNuzjsmhDYAPexZ3FL//2wmUspO8IFgV6dtxQ/PeEMMA3KgqlbbC1j+Qa3bb
bP6MvPJwNQzcmRk13NfIRmPVNnGuV/u3gm3c
-----END CERTIFICATE-----
# Amazon Root CA 1
-----BEGIN CERTIFICATE-----
MIIDQTCCAimgAwIBAgITBmyfz5m/jAo54vB4ikPmljZbyjANBgkqhkiG9w0BAQsF
ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6
b24gUm9vdCBDQSAxMB4XDTE1MDUyNjAwMDAwMFoXDTM4MDExNzAwMDAwMFowOTEL
MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv
b3QgQ0EgMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALJ4gHHKeNXj
ca9HgFB0fW7Y14h29Jlo91ghYPl0hAEvrAIthtOgQ3pOsqTQNroBvo3bSMgHFzZM
9O6II8c+6zf1tRn4SWiw3te5djgdYZ6k/oI2peVKVuRF4fn9tBb6dNqcmzU5L/qw
IFAGbHrQgLKm+a/sRxmPUDgH3KKHOVj4utWp+UhnMJbulHheb4mjUcAwhmahRWa6
VOujw5H5SNz/0egwLX0tdHA114gk957EWW67c4cX8jJGKLhD+rcdqsq08p8kDi1L
93FcXmn/6pUCyziKrlA4b9v7LWIbxcceVOF34GfID5yHI9Y/QCB/IIDEgEw+OyQm
jgSubJrIqg0CAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC
AYYwHQYDVR0OBBYEFIQYzIU07LwMlJQuCFmcx7IQTgoIMA0GCSqGSIb3DQEBCwUA
A4IBAQCY8jdaQZChGsV2USggNiMOruYou6r4lK5IpDB/G/wkjUu0yKGX9rbxenDI
U5PMCCjjmCXPI6T53iHTfIUJrU6adTrCC2qJeHZERxhlbI1Bjjt/msv0tadQ1wUs
N+gDS63pYaACbvXy8MWy7Vu33PqUXHeeE6V/Uq2V8viTO96LXFvKWlJbYK8U90vv
o/ufQJVtMVT8QtPHRh8jrdkPSHCa2XV4cdFyQzR1bldZwgJcJmApzyMZFo6IQ6XU
5MsI+yMRQ+hDKXJioaldXgjUkK642M4UwtBV8ob2xJNDd2ZhwLnoQdeXeGADbkpy
rqXRfboQnoZsG4q5WTP468SQvvG5
-----END CERTIFICATE-----
# Starfield Services Root Certificate Authority - G2
-----BEGIN CERTIFICATE-----
MIID7zCCAtegAwIBAgIBADANBgkqhkiG9w0BAQsFADCBmDELMAkGA1UEBhMCVVMx
EDAOBgNVBAgTB0FyaXpvbmExEzARBgNVBAcTClNjb3R0c2RhbGUxJTAjBgNVBAoT
HFN0YXJmaWVsZCBUZWNobm9sb2dpZXMsIEluYy4xOzA5BgNVBAMTMlN0YXJmaWVs
ZCBTZXJ2aWNlcyBSb290IENlcnRpZmljYXRlIEF1dGhvcml0eSAtIEcyMB4XDTA5
MDkwMTAwMDAwMFoXDTM3MTIzMTIzNTk1OVowgZgxCzAJBgNVBAYTAlVTMRAwDgYD
VQQIEwdBcml6b25hMRMwEQYDVQQHEwpTY290dHNkYWxlMSUwIwYDVQQKExxTdGFy
ZmllbGQgVGVjaG5vbG9naWVzLCBJbmMuMTswOQYDVQQDEzJTdGFyZmllbGQgU2Vy
dmljZXMgUm9vdCBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkgLSBHMjCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBANUMOsQq+U7i9b4Zl1+OiFOxHz/Lz58gE20p
OsgPfTz3a3Y4Y9k2YKibXlwAgLIvWX/2h/klQ4bnaRtSmpDhcePYLQ1Ob/bISdm2
8xpWriu2dBTrz/sm4xq6HZYuajtYlIlHVv8loJNwU4PahHQUw2eeBGg6345AWh1K
Ts9DkTvnVtYAcMtS7nt9rjrnvDH5RfbCYM8TWQIrgMw0R9+53pBlbQLPLJGmpufe
hRhJfGZOozptqbXuNC66DQO4M99H67FrjSXZm86B0UVGMpZwh94CDklDhbZsc7tk
6mFBrMnUVN+HL8cisibMn1lUaJ/8viovxFUcdUBgF4UCVTmLfwUCAwEAAaNCMEAw
DwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFJxfAN+q
AdcwKziIorhtSpzyEZGDMA0GCSqGSIb3DQEBCwUAA4IBAQBLNqaEd2ndOxmfZyMI
bw5hyf2E3F/YNoHN2BtBLZ9g3ccaaNnRbobhiCPPE95Dz+I0swSdHynVv/heyNXB
ve6SbzJ08pGCL72CQnqtKrcgfU28elUSwhXqvfdqlS5sdJ/PHLTyxQGjhdByPq1z
qwubdQxtRbeOlKyWN7Wg0I8VRw7j6IPdj/3vQQF3zCepYoUz8jcI73HPdwbeyBkd
iEDPfUYd/x7H4c7/I9vG+o1VTqkC50cRRj70/b17KSa7qWFiNyi2LSr2EIZkyXCn
0q23KXB56jzaYyWf/Wi3MOxw+3WKt21gZ7IeyLnp2KhvAotnDU0mV3HaIPzBSlCN
sSi6
-----END CERTIFICATE-----
# ISRG Root X1
-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
# DigiCert Global Root G2
-----BEGIN CERTIFICATE-----
MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI
2/Ou8jqJkTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx
1x7e/dfgy5SDN67sH0NO3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQ
q2EGnI/yuum06ZIya7XzV+hdG82MHauVBJVJ8zUtluNJbd134/tJS7SsVQepj5Wz
tCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyMUNGPHgm+F6HmIcr9g+UQ
vIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQABo0IwQDAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV
5uNu5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY
1Yl9PMWLSn/pvtsrF9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4
NeF22d+mQrvHRAiGfzZ0JFrabA0UWTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NG
Fdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBHQRFXGU7Aj64GxJUTFy8bJZ91
8rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/iyK5S9kJRaTe
pLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl
MrY=
-----END CERTIFICATE-----
"""


# ── output / log ────────────────────────────────────────────────

def say(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "ignore").decode("ascii"), flush=True)
    try:
        log.info(msg)
    except Exception:
        pass


def setup_log():
    folder = os.path.join(HERE, "tokens_logs")
    os.makedirs(folder, exist_ok=True)
    if not log.handlers:
        fh = logging.FileHandler(os.path.join(folder, f"{datetime.now(IST):%Y-%m-%d}_FS_Status_AWS.log"), encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s IST - %(levelname)s - %(message)s")
        fmt.converter = lambda *_: datetime.now(IST).timetuple()   # IST even on a UTC server
        fh.setFormatter(fmt)
        log.addHandler(fh)
        log.setLevel(logging.INFO)
        log.propagate = False


# ── credentials ─────────────────────────────────────────────────

def read_creds(path):
    """'KEY = value' lines (# = comment). Keys upper-cased, spaces -> '_' (same rule as Firstock_Config.py)."""
    out = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out["_".join(k.upper().split())] = v.strip()
    if not out.get("TELEGRAM_GROUP_CHAT_ID") and out.get("TELEGRAM_CHAT_IDS"):
        out["TELEGRAM_GROUP_CHAT_ID"] = out["TELEGRAM_CHAT_IDS"]
    return out


def ip_ok(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if os.name != "nt":
        os.chmod(path, 0o600)       # only this Linux user can read it


def setup(path):
    """Ask every value in the terminal (secrets are not shown while typing) and save Firstock_Credentials.txt."""
    if not sys.stdin or not sys.stdin.isatty():
        sys.exit(f"{CRED_FILE} is missing and there is no terminal to ask in. "
                 f"Run once by hand: python3 {os.path.basename(__file__)} --setup")
    old = read_creds(path) if os.path.exists(path) else {}
    print(f"\nCredentials -> {path}")
    print("Hidden fields show nothing while you type or paste; just press Enter at the end.\n")
    new = {}
    for key, question, secret, required in FIELDS:
        current = old.get(key, "")
        hint = " [Enter = keep current]" if current else ("" if required else " [Enter = skip]")
        while True:
            prompt = f"{question}{hint}: "
            value = (getpass.getpass(prompt) if secret else input(prompt)).strip() or current
            if required and not value:
                print("  this one is needed")
            elif key == "TOTP_KEY" and not totp_ok(value):
                print("  not a TOTP secret (letters A-Z and digits 2-7)")
            elif key in ("STATIC_IP", "SOURCE_IP") and value and not ip_ok(value):
                print("  not an IP address")
            else:
                break
        new[key] = value
    extra = {k: v for k, v in old.items() if k not in new and k != "TELEGRAM_CHAT_IDS"}
    lines = ["# Firstock login for Firstock_Status_AWS.py (same format as Firstock_Config.py). Keep this file private."]
    lines += [f"{k} = {v}" for k, v in list(new.items()) + list(extra.items())]
    write_private(path, "\n".join(lines) + "\n")
    print(f"\nSaved {path}\n")


# ── TOTP (same result as pyotp.TOTP(key).now()) ─────────────────

def _b32(secret):
    s = "".join(secret.split()).upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp_ok(secret):
    try:
        return len(_b32(secret)) > 0
    except Exception:
        return False


def totp(secret, at=None):
    counter = int(time.time() if at is None else at) // 30
    digest = hmac.new(_b32(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", digest[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


# ── HTTP ────────────────────────────────────────────────────────

_ssl_ctx = None


def ssl_context():
    """The machine's trusted roots + EXTRA_ROOTS (certificate checks stay on)."""
    global _ssl_ctx
    if _ssl_ctx is None:
        ctx = ssl.create_default_context()
        try:
            ctx.load_verify_locations(cadata=EXTRA_ROOTS)
        except (ssl.SSLError, ValueError) as e:
            say(f"[ssl] extra root certificates not loaded: {e}")
        _ssl_ctx = ctx
    return _ssl_ctx


def request(method, url, body=None, timeout=15):
    """(HTTP status, body text). Raises on network errors. Sends from SOURCE_IP when that is set."""
    u = urllib.parse.urlsplit(url)
    src = C.get("SOURCE_IP")
    kw = {"timeout": timeout, "source_address": (src, 0) if src else None}
    if u.scheme == "https":
        conn = http.client.HTTPSConnection(u.hostname, u.port, context=ssl_context(), **kw)
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port, **kw)
    try:
        headers = {"User-Agent": "Firstock-Status/1.0", "Accept": "*/*"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, (u.path or "/") + ("?" + u.query if u.query else ""), body=data, headers=headers)
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        conn.close()


def public_ip():
    """(this machine's public IP, None) or (None, reason)."""
    try:
        status, text = request("GET", "https://api.ipify.org", timeout=8)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    if status == 200 and ip_ok(text.strip()):
        return text.strip(), None
    return None, f"HTTP {status}"


def telegram(text):
    """Send to every chat id. Never raises; failures go to the screen / log."""
    if not SEND_TELEGRAM:
        return
    token = C.get("TELEGRAM_BOT_TOKEN", "")
    chats = [x.strip() for x in C.get("TELEGRAM_GROUP_CHAT_ID", "").split(",") if x.strip()]
    if not token or not chats:
        say("[telegram] bot token / chat id not set - message not sent")
        return
    for chat in chats:
        try:
            status, body = request("POST", f"https://api.telegram.org/bot{token}/sendMessage",
                                   {"chat_id": chat, "text": text[:4000]}, timeout=10)
            if status != 200:
                say(f"[telegram] send to {chat} failed: HTTP {status} {body[:200]}")
        except Exception as e:
            say(f"[telegram] send to {chat} failed: {type(e).__name__}: {str(e).replace(token, '***')}")


# ── Firstock API (read-only calls + login) ──────────────────────

class Firstock:
    def __init__(self, user_id):
        self.user_id = user_id
        self.jkey = None
        self.last_status = None

    def post(self, endpoint, payload, timeout=15):
        """Parsed JSON reply, or None when there was no usable reply."""
        try:
            status, text = request("POST", BASE_URL + endpoint, payload, timeout)
            self.last_status = status
        except Exception as e:
            self.last_status = None
            say(f"[{endpoint}] no response: {type(e).__name__}: {e}")
            return None
        try:
            return json.loads(text)
        except ValueError:
            say(f"[{endpoint}] HTTP {status}, reply is not JSON: {text[:200]!r}")
            return None

    def call(self, endpoint, **fields):
        payload = {"userId": self.user_id, "jKey": self.jkey}
        payload.update(fields)
        resp = self.post(endpoint, payload)
        if isinstance(resp, dict) and resp.get("status") == "success":
            return resp
        if isinstance(resp, dict) and resp.get("name") == "NO_DATA":
            return {"status": "success", "data": []}    # e.g. no orders today: the call worked, the list is empty
        if resp is not None:
            say(f"[{endpoint}] not success (HTTP {self.last_status}): {str(resp)[:300]}")
        return None

    @staticmethod
    def _rows(resp):
        data = resp.get("data")
        if data is None:
            return []
        return data if isinstance(data, list) else [data]

    def history(self, days):
        """NIFTY daily candles of the last `days` days: list (may be empty), or None if the call failed."""
        now = datetime.now(IST)   # Firstock reads these times as IST, the server clock may be UTC
        resp = self.call("/timePriceSeries", exchange="NSE", tradingSymbol="NIFTY",
                         startTime=(now - timedelta(days=days)).strftime("%H:%M:%S %d-%m-%Y"),
                         endTime=now.strftime("%H:%M:%S %d-%m-%Y"), interval="1d")
        return None if resp is None else self._rows(resp)

    def ping(self):
        """NIFTY LTP (previous close when the market is shut), or None if the session / API does not answer."""
        resp = self.call("/getQuote/ltp", exchange="NSE", tradingSymbol="NIFTY")
        if resp is None:
            return None
        try:
            rows = self._rows(resp)
            ltp = float(rows[0]["lastTradedPrice"])
        except Exception:
            return None
        if ltp > 0:
            return ltp
        candles = self.history(5) or []
        try:
            return float(candles[-1]["close"]) if candles else 0.0
        except Exception:
            return 0.0

    def positions(self):
        resp = self.call("/positionBook")
        return None if resp is None else self._rows(resp)

    def orders(self):
        resp = self.call("/orderBook")
        return None if resp is None else self._rows(resp)

    def limits(self):
        return self.call("/limit")


class FileLock:
    """Same lock file as the strategies (<token file>.lock, filelock style), so only one process logs in at a time.
    Gives up after `timeout` seconds and goes on without the lock."""

    def __init__(self, path, timeout=60):
        self.path, self.timeout, self.fd = path, timeout, None

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        except OSError as e:
            say(f"[lock] cannot open {self.path}: {e} - going on without it")
            return self
        end = time.time() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = fd
                return self
            except OSError:
                if time.time() > end:
                    say("[lock] another process holds the login lock for 60 s - going on without it")
                    os.close(fd)
                    return self
                time.sleep(0.5)

    def __exit__(self, *exc):
        if self.fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, 0)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self.fd)


def read_key(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def key_works(api):
    """Is api.jkey alive? Quote + history, 3 tries 5 s apart; when every quote fails, /positionBook decides."""
    for i in range(1, VERIFY_TRIES + 1):
        if api.ping() is None:
            reason = "quote failed"
        elif api.history(2) is None:
            reason = f"history failed (HTTP {api.last_status})"
        else:
            say(f"  check {i}/{VERIFY_TRIES}: OK")
            return True
        say(f"  check {i}/{VERIFY_TRIES}: {reason}")
        if i < VERIFY_TRIES:
            time.sleep(VERIFY_WAIT)
    alive = api.positions() is not None
    say(f"  /positionBook fallback: {'OK - session alive' if alive else 'FAILED - session dead'}")
    return alive


def login(api, token_file):
    """Use the saved key while it works, else one fresh login (max LOGIN_TRIES). Returns how it logged in.
    Raises RuntimeError with the broker's reason when the login fails."""
    stale = read_key(token_file)
    if stale:
        say(f"Saved session key found ({os.path.basename(token_file)}) - checking it ...")
        api.jkey = stale
        if key_works(api):
            return "saved key"
        say("Saved key is not working - fresh login.")
    else:
        say("No saved session key - fresh login.")

    with FileLock(token_file + ".lock"):
        now = read_key(token_file)
        if now and now != stale:          # another process logged in while we waited
            say("Another process wrote a new key - checking it ...")
            api.jkey = now
            if key_works(api):
                return "key from another process"
        reason = "unknown"
        for attempt in range(1, LOGIN_TRIES + 1):
            resp = api.post("/login", {
                "userId": C["USERID"],
                "password": hashlib.sha256(C["PASSWORD"].encode()).hexdigest(),
                "TOTP": totp(C["TOTP_KEY"]),
                "vendorCode": C["API_SECRET"],
                "apiKey": C["API_KEY"],
            })
            # never log the raw login reply: on success it carries the session key
            key = ""
            if isinstance(resp, dict) and resp.get("status") == "success":
                key = str((resp.get("data") or {}).get("susertoken") or "")
            if key:
                tmp = token_file + ".tmp"
                write_private(tmp, key)
                os.replace(tmp, token_file)
                api.jkey = key
                say(f"LOGIN SUCCESS - new key ...{key[-6:]} saved to {os.path.basename(token_file)}")
                return "fresh login"
            if resp is None:
                reason = "no response from Firstock"
            else:
                err = resp.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else err
                reason = f"{resp.get('name') or resp.get('code') or resp.get('status')}: {msg}"
            say(f"Login attempt {attempt}/{LOGIN_TRIES} failed - {reason}")
            if attempt < LOGIN_TRIES:
                time.sleep(5)
    raise RuntimeError(reason)


# ── status report ───────────────────────────────────────────────

def market_open():
    now = datetime.now(IST)
    return now.weekday() < 5 and 9 * 60 + 15 <= now.hour * 60 + now.minute < 15 * 60 + 30


def first(row, keys):
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return "?"


def is_open(row):
    try:
        return float(first(row, QTY_KEYS)) != 0
    except (TypeError, ValueError):
        return True        # quantity not understood: never report it as closed


def capped(rows, render):
    out = [render(r) for r in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        out.append(f"  ... and {len(rows) - MAX_ROWS} more")
    return out


def funds_lines(resp):
    if resp is None:
        return None
    data = resp.get("data")
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return None
    items = [(k, v) for k, v in data.items()
             if v not in (None, "") and not any(h in str(k).lower() for h in HIDDEN)]
    return capped(items, lambda kv: f"  {kv[0]}: {kv[1]}")


def run_check():
    """Full account status: printed, logged and sent to Telegram. 0 = all fine, 1 = something is wrong."""
    name = C["USERID"]
    problems = []
    lines = [f"📊 STATUS CHECK — {datetime.now(IST):%d-%b-%Y %H:%M:%S} IST",
             f"Machine: {socket.gethostname()}",
             f"Market: {'OPEN' if market_open() else 'CLOSED'}"]

    (ip, why), expected = public_ip(), C.get("STATIC_IP", "")
    if ip is None:
        lines.append(f"Static IP: could not check ({why})")
    elif not expected:
        lines.append(f"Public IP: {ip} (STATIC_IP not set)")
    elif ip == expected:
        lines.append(f"Static IP: match ({ip})")
    else:
        lines.append(f"Static IP: MISMATCH — this machine {ip}, Firstock static IP {expected}")
        problems.append("static IP mismatch")

    api = Firstock(name)
    try:
        how = login(api, os.path.join(HERE, f"{name}_jkey.txt"))
    except Exception as e:
        lines += [f"Login: FAILED ({e})", "Overall: PROBLEM — login failed, account not checked"]
        report(lines, name)
        return 1
    lines.append(f"Login: OK ({how})")

    ltp = api.ping()
    lines.append(f"NIFTY LTP: {ltp}" if ltp else "NIFTY LTP: FAILED")
    if not ltp:
        problems.append("NIFTY LTP")

    funds = funds_lines(api.limits())
    if funds is None:
        lines.append("Funds: FAILED")
        problems.append("funds")
    else:
        lines += ["Funds:"] + funds

    positions = api.positions()
    if positions is None:
        lines.append("Positions: FAILED (could not verify)")
        problems.append("positions")
    else:
        open_pos = [p for p in positions if isinstance(p, dict) and is_open(p)]
        lines.append(f"Open positions: {len(open_pos)}")
        lines += capped(open_pos, lambda p: f"  {p.get('tradingSymbol', '?')}: net qty {first(p, QTY_KEYS)}")

    orders = api.orders()
    if orders is None:
        lines.append("Orders: FAILED")
        problems.append("orders")
    else:
        counts = {}
        for o in orders:
            s = str(o.get("status", "?")).upper() if isinstance(o, dict) else "?"
            counts[s] = counts.get(s, 0) + 1
        detail = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        lines.append(f"Orders today: {len(orders)}" + (f" ({detail})" if detail else ""))

    lines.append("Overall: ALL OK" if not problems else "Overall: PROBLEM — " + ", ".join(problems))
    report(lines, name)
    return 1 if problems else 0


def report(lines, name):
    text = "\n".join(lines)
    say("\n" + text)
    telegram(f"[STATUS][{str(name).upper()}] {text}")


def main():
    global SEND_TELEGRAM
    ap = argparse.ArgumentParser(description="Firstock read-only status check (one file, no pip install).")
    ap.add_argument("--setup", action="store_true", help="enter / change the credentials, then run the check")
    ap.add_argument("--no-telegram", action="store_true", help="print only, send nothing to Telegram")
    a = ap.parse_args()
    SEND_TELEGRAM = not a.no_telegram
    setup_log()

    path = os.path.join(HERE, CRED_FILE)
    if a.setup or not os.path.exists(path):
        if not os.path.exists(path):
            print(f"No {CRED_FILE} next to this file - creating it now (one time only).")
        setup(path)
    C.update(read_creds(path))
    missing = [k for k, _, _, required in FIELDS if required and not C.get(k)]
    if missing:
        sys.exit(f"Missing in {CRED_FILE}: {', '.join(missing)} - run: python3 {os.path.basename(__file__)} --setup")
    if not totp_ok(C["TOTP_KEY"]):
        sys.exit(f"TOTP_KEY in {CRED_FILE} is not a valid TOTP secret - run with --setup")

    try:
        return run_check()
    except KeyboardInterrupt:
        raise
    except Exception as e:
        msg = f"🚨 STATUS CHECK CRASHED: {type(e).__name__}: {e}"
        say(msg)
        telegram(f"[STATUS][SYSTEM] {msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
