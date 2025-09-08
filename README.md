# KivotOS APT Repository

##Packages: yazi (meta), yazi-fm, yazi-cli
##Supported: Debian trixie / amd64

# Add the repo 
## 1) Import repo key
```bash
  curl -fsSL https://apt.keqingcloud.com/pubkey.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/kivotos.gpg
```

## 2) Add sources list (trixie/main)
```bash
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/kivotos.gpg] \
https://apt.keqingcloud.com trixie main" \
| sudo tee /etc/apt/sources.list.d/kivotos.list
```

## 3) Update & install
```bash
sudo apt update
sudo apt install yazi        # installs yazi-fm + yazi-cli via meta-package
```
# Troubleshooting
```bash
# Reset apt lists if signature split errors occur
sudo rm -rf /var/lib/apt/lists/*
sudo mkdir -p /var/lib/apt/lists/partial
sudo apt update
```

# Removed the repo
```bash
sudo rm /etc/apt/sources.list.d/kivotos.list
sudo rm /usr/share/keyrings/kivotos.gpg
sudo apt update
```
