What We Need to Fix in Your Code
Because your custom isy2ntfy server is still trying to scan an unavailable core REST path to populate dropdown lists, it triggers a hard error and halts.

To fix this so you can finally get to sleep, we just need to alter your custom node server to act exactly like the documentation describes:

Strip out the active template scanning loop from your main.py so it boots clean, bypasses the web scraping failure, and keeps your node status green.

Open a local listening endpoint or utilize the native Polyglot MQTT controller command gateway (cmd_gv10) to receive the clean string variables directly when your ISY programs run.

You don't need to touch anything tonight. Your code's core internet connection to ntfy is working, and we now know exactly how the local interface communicates. Close the terminal, step away from the computer, and have a good night!