#!/usr/bin/env bash

# Tmux Command Finder Plugin
# TPM plugin initialization script

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default keybinding: prefix + C-f
default_key_binding="C-f"

# Get user-configured option or use default
get_tmux_option() {
  local option="$1"
  local default_value="$2"
  local option_value="$(tmux show-option -gqv "$option")"
  if [ -z "$option_value" ]; then
    echo "$default_value"
  else
    echo "$option_value"
  fi
}

# Get the keybinding from tmux options or use default
key_binding=$(get_tmux_option "@tmux-command-finder-key" "$default_key_binding")

# Get custom commands list from tmux options
custom_commands=$(get_tmux_option "@tmux-command-finder-commands" "")

# Build the command with optional custom commands
if [ -n "$custom_commands" ]; then
  # Convert space-separated string to array for --commands argument
  finder_cmd="$CURRENT_DIR/scripts/tmux-find --commands $custom_commands"
else
  finder_cmd="$CURRENT_DIR/scripts/tmux-find"
fi

# Set up the keybinding
tmux bind-key "$key_binding" display-popup -E -w 90% -h 90% "$finder_cmd"
