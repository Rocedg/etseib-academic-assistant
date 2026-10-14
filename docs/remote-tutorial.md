# Remote classroom tutorial

The confirmed access route is RDP through the UPC VPN. Obtain the official VPN client, remote-PC address, and schedule from the ETSEIB or UPC page; this repository does not invent those values.

1. Connect to the UPC VPN.
2. Open Remote Desktop (`mstsc` on Windows or Windows App on macOS).
3. Connect with your UPC account and enable clipboard or a redirected drive if the classroom policy allows it.
4. Copy this repository to the remote machine and run `scripts/classroom/run_test.bat`.
5. Copy the resulting file from `results/` to the course thread.

Do not put credentials, private addresses, or personal paths in committed files. If Python, network access, or Ollama is unavailable, run the Ubuntu classroom test or use the local mock commands in the root README.
