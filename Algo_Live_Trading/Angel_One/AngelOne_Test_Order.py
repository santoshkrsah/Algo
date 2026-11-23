#!/usr/bin/env python3
"""
AngelOne_Test_Order.py - ONE small test order on Angel One from this machine, cancelled right away.

Checks the whole order path from this VM: static IP, TOTP login, placeOrder, order status, cancelOrder,
and sends the result to Telegram.
    BUY 1 share (default IDEA-EQ on NSE), LIMIT price 5% below the last price, DELIVERY (CNC),
    then it is cancelled at once. A limit 5% under the market does not fill in those 1-2 seconds.
A rejection is also a result: the reason shows where it stopped (IP, market closed, margin ...).
When the market is closed (evenings, weekends) the order is most likely refused. The full check is a run in
market hours (Mon-Fri 09:15-15:30 IST). After market hours you can also try:  --variety AMO

    py AngelOne_Test_Order.py                          IDEA-EQ, 1 share
    py AngelOne_Test_Order.py --symbol YESBANK-EQ      another cheap NSE share (NSE shares end with -EQ)
    py AngelOne_Test_Order.py --variety AMO            after-market order
    py AngelOne_Test_Order.py --qty 2 --product INTRADAY
    py AngelOne_Test_Order.py --no-telegram            print only, send nothing to Telegram
    py AngelOne_Test_Order.py --dry-run                login + price + show the order, place nothing

Needs only AngelOne_Credentials.txt in the same folder (python standard library, nothing to install).
It shows the order first and places it only after you type YES. It never re-sends an order.
"""
import argparse
import base64
import hashlib
import hmac
import http.client
import json
import math
import os
import socket
import ssl
import struct
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = "AngelOne_Credentials.txt"
HOST = "apiconnect.angelone.in"
IST = timezone(timedelta(hours=5, minutes=30))
BELOW = 0.05                                   # limit price this far under the last price
FINAL = ("COMPLETE", "REJECTED", "CANCELLED", "CANCELED")
SEND_TELEGRAM = True
C = {}

# Public root certificates (Mozilla list, same as Firstock_Status_AWS.py), trusted IN ADDITION to the machine's own list.
# A fresh AWS Windows VM has few roots: Python then fails with "CERTIFICATE_VERIFY_FAILED". Checks stay fully on.
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

_ctx = None


def say(msg):
    print(msg, flush=True)


def ssl_context():
    global _ctx
    if _ctx is None:
        _ctx = ssl.create_default_context()
        try:
            _ctx.load_verify_locations(cadata=EXTRA_ROOTS)
        except (ssl.SSLError, ValueError) as e:
            say(f"[ssl] extra root certificates not loaded: {e}")
    return _ctx


def read_creds(path):
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out["_".join(k.upper().split())] = v.strip()
    return out


def totp(secret, step=30):
    s = secret.replace(" ", "").upper()
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    h = hmac.new(key, struct.pack(">Q", int(time.time()) // step), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


def https(method, host, path, body=None, headers=None, timeout=20):
    """(HTTP status, parsed JSON or text). Raises on network errors."""
    conn = http.client.HTTPSConnection(host, context=ssl_context(), timeout=timeout)
    try:
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
    finally:
        conn.close()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def public_ip():
    """(ip, None) or (None, reason)."""
    try:
        st, txt = https("GET", "api.ipify.org", "/", timeout=10)
        return (txt.strip(), None) if st == 200 and isinstance(txt, str) else (None, f"HTTP {st}")
    except Exception as e:
        return None, str(e)[:100]


def telegram(text):
    if not SEND_TELEGRAM:
        return
    token = C.get("TELEGRAM_BOT_TOKEN")
    chats = [x.strip() for x in (C.get("TELEGRAM_CHAT_IDS") or C.get("TELEGRAM_GROUP_CHAT_ID") or "").split(",") if x.strip()]
    if not token or not chats:
        say("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_GROUP_CHAT_ID missing - not sent")
        return
    for chat in chats:
        try:
            st, _ = https("POST", "api.telegram.org", f"/bot{token}/sendMessage",
                          {"chat_id": chat, "text": text[:4000]}, {"Content-Type": "application/json"}, timeout=15)
            if st != 200:
                say(f"[telegram] chat {chat}: HTTP {st}")
        except Exception as e:
            say(f"[telegram] chat {chat}: {str(e)[:100]}")


class Angel:
    def __init__(self, ip):
        self.ip, self.jwt = ip or "127.0.0.1", None

    def call(self, path, body=None):
        """Parsed JSON dict, or None when there was no usable reply (network error)."""
        h = {"Content-Type": "application/json", "Accept": "application/json", "X-UserType": "USER",
             "X-SourceID": "WEB", "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": self.ip,
             "X-MACAddress": "00:00:00:00:00:00", "X-PrivateKey": C["API_KEY"]}
        if self.jwt:
            h["Authorization"] = "Bearer " + self.jwt
        try:
            st, data = https("POST" if body is not None else "GET", HOST, path, body, h)
        except Exception as e:
            say(f"[{path.rsplit('/', 1)[-1]}] network error: {str(e)[:150]}")
            return None
        return data if isinstance(data, dict) else {"status": False, "message": f"HTTP {st}", "raw": str(data)[:200]}

    def login(self):
        r = self.call("/rest/auth/angelbroking/user/v1/loginByPassword",
                      {"clientcode": C["CLIENT_CODE"], "password": C["PIN"], "totp": totp(C["TOTP_KEY"])})
        if not r or not r.get("status"):
            raise RuntimeError(f"{(r or {}).get('message') or 'no reply'} {(r or {}).get('errorcode') or ''}".strip())
        self.jwt = r["data"]["jwtToken"]

    def orders(self):
        r = self.call("/rest/secure/angelbroking/order/v1/getOrderBook")
        return (r.get("data") or []) if r and r.get("status") else None


def market_open():
    n = datetime.now(IST)
    return n.weekday() < 5 and (9, 15) <= (n.hour, n.minute) < (15, 30)


def tick_floor(price):
    """Round DOWN to a valid NSE price step: 0.05 under Rs 250 (fits the 0.01 and 0.05 ticks), else whole rupees."""
    step = 0.05 if price < 250 else 1.0
    return max(round(math.floor(price / step + 1e-9) * step, 2), 0.05)


def find_symbol(api, symbol):
    """Token of an NSE cash symbol from the instrument search, or None."""
    r = api.call("/rest/secure/angelbroking/order/v1/searchScrip",
                 {"exchange": "NSE", "searchscrip": symbol.replace("-EQ", "")})
    for row in (r or {}).get("data") or []:
        if str(row.get("tradingsymbol", "")).upper() == symbol:
            return str(row.get("symboltoken"))
    return None


def last_price(api, symbol, token):
    """(price, live) - live LTP, else the previous close; (None, False) if not found."""
    r = api.call("/rest/secure/angelbroking/order/v1/getLtpData",
                 {"exchange": "NSE", "tradingsymbol": symbol, "symboltoken": token})
    try:
        d = r["data"]
        ltp = float(d.get("ltp") or 0)
        if ltp > 0:
            return ltp, True
        return float(d.get("close") or 0) or None, False
    except Exception:
        return None, False


def order_state(api, oid, tries):
    """(STATUS, reason, row) from the order book, read once a second until a final status or `tries` reads."""
    status, reason, row = "UNKNOWN", "", {}
    for _ in range(tries):
        time.sleep(1)
        for o in api.orders() or []:
            if str(o.get("orderid")) == oid:
                row = o
                status = str(o.get("orderstatus") or o.get("status") or "UNKNOWN").upper()
                reason = str(o.get("text") or "").strip()
        if status in FINAL:
            break
    return status, reason, row


def find_unacked(api, symbol, price, qty):
    """placeOrder gave no reply: is the order in the order book anyway? Its id, or None."""
    for _ in range(3):
        time.sleep(2)
        book = api.orders()
        if book is None:
            continue
        same = [o for o in book if str(o.get("tradingsymbol")) == symbol and str(o.get("transactiontype")).upper() == "BUY"
                and str(o.get("quantity")) == str(qty) and abs(float(o.get("price") or 0) - price) < 0.001]
        if len(same) == 1 and same[0].get("orderid"):
            return str(same[0]["orderid"])
    return None


def verdict(reason, market_is_open):
    low = (reason or "").lower()
    if "static" in low or "whitelist" in low or " ip" in low or low.startswith("ip"):
        return "Result: looks like an IP problem - register this machine's public IP in the SmartAPI app"
    if not market_is_open:
        return ("Result: Angel One answered the order from this machine (login + order API work). Market is closed: "
                "run again in market hours (Mon-Fri 09:15-15:30 IST) for the full check.")
    return "Result: the order API answered from this machine; the reason above is why it was not accepted (e.g. margin)."


def finish(lines, code):
    text = "\n".join(lines)
    say("\n" + text)
    telegram(f"[TEST_ORDER][ANGEL_ONE] {text}")
    return code


def main():
    global SEND_TELEGRAM
    ap = argparse.ArgumentParser(description="Place ONE small test order (NSE, limit 5% below) and cancel it at once.")
    ap.add_argument("--symbol", default="IDEA-EQ", help="NSE trading symbol, default IDEA-EQ")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--product", default="DELIVERY", choices=["DELIVERY", "INTRADAY"])
    ap.add_argument("--variety", default="NORMAL", choices=["NORMAL", "AMO"])
    ap.add_argument("--dry-run", action="store_true", help="login + price + show the order, place nothing")
    ap.add_argument("--no-telegram", action="store_true", help="print only, send nothing to Telegram")
    a = ap.parse_args()
    SEND_TELEGRAM = not a.no_telegram

    path = os.path.join(HERE, CRED_FILE)
    if not os.path.exists(path):
        sys.exit(f"{CRED_FILE} not found next to this file.")
    C.update(read_creds(path))
    missing = [k for k in ("CLIENT_CODE", "PIN", "TOTP_KEY", "API_KEY") if not C.get(k)]
    if missing:
        sys.exit(f"Missing in {CRED_FILE}: {', '.join(missing)}")
    if not a.dry_run and (not sys.stdin or not sys.stdin.isatty()):
        sys.exit("This places a REAL order, so it only runs by hand in a terminal (it asks for YES first).")

    symbol, market_is_open = a.symbol.strip().upper(), market_open()
    lines = [f"🧪 TEST ORDER (Angel One) — {datetime.now(IST):%d-%b-%Y %H:%M:%S} IST",
             f"Machine: {socket.gethostname()}",
             f"Market: {'OPEN' if market_is_open else 'CLOSED'}"]
    (ip, why), expected = public_ip(), C.get("STATIC_IP", "")
    if ip is None:
        lines.append(f"Static IP: could not check ({why})")
    elif expected and ip != expected:
        lines.append(f"Static IP: MISMATCH — this machine {ip}, registered static IP {expected}")
    else:
        lines.append(f"Static IP: {'match' if expected else 'not set'} ({ip})")

    api = Angel(ip)
    try:
        api.login()
    except Exception as e:
        lines += [f"Login: FAILED ({e})", "Result: no order placed"]
        return finish(lines, 1)
    lines.append("Login: OK")

    token = find_symbol(api, symbol)
    if not token:
        lines += [f"Symbol {symbol}: not found (NSE shares end with -EQ, e.g. IDEA-EQ)", "Result: no order placed"]
        return finish(lines, 1)
    last, live = last_price(api, symbol, token)
    if not last:
        lines += [f"Price of {symbol}: not found", "Result: no order placed"]
        return finish(lines, 1)
    if market_is_open and not live:
        lines += [f"{symbol}: no live price in market hours", "Result: no order placed"]
        return finish(lines, 1)
    price = tick_floor(last * (1 - BELOW))
    order = (f"BUY {a.qty} {symbol} (NSE) LIMIT {price:.2f} "
             f"({BELOW:.0%} under the {'last price' if live else 'previous close'} {last:.2f}), {a.product}, {a.variety}")

    print("\n" + "\n".join(lines))
    if a.dry_run:
        print(f"\nDRY RUN - would place: {order}\nNothing sent.")
        return 0
    print(f"\nThis is a REAL order on account {C['CLIENT_CODE']}:\n  {order}\n  It is cancelled right after it is placed.")
    if not market_is_open and a.variety == "NORMAL":
        print("  Market is CLOSED now: Angel One will most likely refuse it (try --variety AMO) - that is fine for this test.")
    if input("Type YES to place it: ").strip() != "YES":
        print("Not placed (you did not type YES).")
        return 0
    lines.append(f"Order: {order}")

    # Sent exactly ONCE, never retried: a retry after a lost reply could make a second order.
    resp = api.call("/rest/secure/angelbroking/order/v1/placeOrder", {
        "variety": a.variety, "tradingsymbol": symbol, "symboltoken": token, "transactiontype": "BUY",
        "exchange": "NSE", "ordertype": "LIMIT", "producttype": a.product, "duration": "DAY",
        "price": f"{price:.2f}", "squareoff": "0", "stoploss": "0", "quantity": str(a.qty)})
    say(f"[placeOrder] reply: {str(resp)[:500]}")
    if resp is None:
        oid = find_unacked(api, symbol, price, a.qty)
        if not oid:
            lines += ["Placed: NO REPLY from Angel One, and the order book does not show it",
                      "🚨 Result: UNKNOWN — open the Angel One app and check the order list"]
            return finish(lines, 1)
        lines.append(f"Placed: no reply, but order {oid} is in the order book")
    elif not resp.get("status"):
        reason = f"{resp.get('errorcode') or ''} {resp.get('message') or resp.get('raw') or ''}".strip()
        lines += [f"Placed: REFUSED by Angel One — {reason}", verdict(reason, market_is_open)]
        return finish(lines, 0)
    else:
        oid = str((resp.get("data") or {}).get("orderid") or "")
        if not oid:
            lines += ["Placed: Angel One said success but gave no order id",
                      "🚨 Result: UNKNOWN — open the Angel One app and check the order list"]
            return finish(lines, 1)
        lines.append(f"Placed: OK — order id {oid}")

    status, reason, row = order_state(api, oid, tries=2)
    lines.append(f"Status: {status}" + (f" — {reason}" if reason else ""))
    if status == "REJECTED":
        lines.append(verdict(reason, market_is_open))
        return finish(lines, 0)
    if status in ("CANCELLED", "CANCELED"):
        lines.append("Result: the order is already cancelled — nothing is open")
        return finish(lines, 0)
    if status == "COMPLETE":
        lines.append(f"Result: FILLED — {a.qty} share bought at {row.get('averageprice') or price}. The order path works; "
                     f"the share is now in the account.")
        return finish(lines, 0)

    c = api.call("/rest/secure/angelbroking/order/v1/cancelOrder", {"variety": a.variety, "orderid": oid})
    say(f"[cancelOrder] reply: {str(c)[:300]}")
    status, reason, row = order_state(api, oid, tries=5)
    if status in ("CANCELLED", "CANCELED"):
        lines.append("Cancel: OK — order cancelled")
        lines.append("Result: ALL OK — the order went from this machine to the exchange and was cancelled"
                     if market_is_open and a.variety == "NORMAL" else
                     "Result: OK — Angel One took the order and it was cancelled. Run again in market hours "
                     "(Mon-Fri 09:15-15:30 IST) to see it reach the exchange.")
        return finish(lines, 0)
    if status == "COMPLETE":
        lines.append(f"Result: FILLED before the cancel — {a.qty} share bought at {row.get('averageprice') or price}.")
        return finish(lines, 0)
    if status == "REJECTED":
        lines += ["Status after cancel: REJECTED" + (f" — {reason}" if reason else ""), verdict(reason, market_is_open)]
        return finish(lines, 0)
    lines += [f"Cancel: NOT confirmed (status {status})",
              "🚨 Result: the test order may still be OPEN — cancel it in the Angel One app now"]
    return finish(lines, 1)


if __name__ == "__main__":
    sys.exit(main())
