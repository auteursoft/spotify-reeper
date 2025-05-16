# Spotify Reeper

## Setup
1. Make sure you have Python 3 loaded on your computer. This code was implemented and tested using Python 3.13.12 on an Apple Silicon Macbook Pro (circa 2023). Full Hardware ID: 
```
Model Name:	MacBook Pro
Model Identifier:	Mac14,6
Model Number:	MNXA3JA/A
Chip:	Apple M2 Max
Total Number of Cores:	12 (8 performance and 4 efficiency)
Memory:	64 GB
System Firmware Version:	11881.101.1
OS Loader Version:	11881.101.1
Serial Number (system):	H460JG96WY
Hardware UUID:	AF186476-E8CF-5BF8-A2C2-6CC968E02DAE
Provisioning UDID:	00006021-000230DA3A43C01E
Activation Lock Status:	Enabled
```

2. Clone this repository. Fork it first if you want to make contributions. 
3. Create a python virtual environment: `python3 -m venv .venv`
4. Activate the Python virtual environment: `source .venv/bin/activate`
5. Add the necessary external libraries to your Python virtual environment: `pip install -r requirements.txt`
6.	Spotify Developer Account: You’ll need to register an application to obtain your client_id and client_secret. ￼
 - Visit the Spotify Developer Dashboard.
 - Log in with your Spotify account.
 - Click on “Create an App” and follow the prompts.
 - Once created, you’ll find your Client ID and Client Secret in the app’s settings. ￼ ￼
7.	Set Up Redirect URI: Although not used in this script, **Spotify requires a redirect URI to be set.**
 - In your app settings, set the redirect URI to http://localhost:7777/callback or any valid URI.

## Using 
1.	Make sure your config.json file is in the same directory.
2.	Run from the terminal like this:
```
python spreeper.py "https://open.spotify.com/playlist/2aOikiOH69uiC9VHIAODA6?si=d9c7ac2492bf43fa"
```
