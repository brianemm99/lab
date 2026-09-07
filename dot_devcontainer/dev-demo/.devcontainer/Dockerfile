FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

COPY --chown=vscode:vscode --from=jdxcode/mise /usr/local/bin/mise /home/vscode/.local/bin/

# make sure mise is activated in both zsh and bash. Might be overridden by a user's dotfiles.
RUN echo 'eval "$(mise activate bash)"' >> /home/vscode/.bashrc && \
    echo 'eval "$(mise activate zsh)"' >> /home/vscode/.zshrc

# seed known_hosts file. Prevent ssh stalling
RUN install -d -m 700 -o vscode -g vscode /home/vscode/.ssh && \
    ssh-keyscan github.com >> /home/vscode/.ssh/known_hosts && \
    chown vscode:vscode /home/vscode/.ssh/known_hosts

