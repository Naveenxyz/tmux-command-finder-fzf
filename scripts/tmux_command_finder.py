#!/usr/bin/env python3
"""
Tmux Command Finder - A tool to detect and manage running commands in tmux sessions
"""

import subprocess
import json
import sys
import os
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import argparse


@dataclass
class TmuxProcess:
    session_name: str
    window_index: str
    pane_index: str
    pane_pid: str
    current_command: str
    actual_command: Optional[str] = None
    full_command: Optional[str] = None


class TmuxCommandFinder:
    def __init__(self, target_commands: Optional[List[str]] = None):
        self.target_commands = target_commands or [
            'codex', 'claude', 'opencode', 'npm', 'yarn', 'python', 'node', 
            'cargo', 'go', 'java', 'mvn', 'gradle', 'docker', 'kubectl'
        ]
    
    def get_tmux_panes(self) -> List[TmuxProcess]:
        """Get all tmux panes with their basic info"""
        cmd = ['tmux', 'list-panes', '-a', '-F', '#{session_name}:#{window_index}:#{pane_index}:#{pane_current_command}:#{pane_pid}']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        processes = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split(':')
                if len(parts) >= 5:
                    processes.append(TmuxProcess(
                        session_name=parts[0],
                        window_index=parts[1],
                        pane_index=parts[2],
                        current_command=parts[3],
                        pane_pid=parts[4]
                    ))
        
        return processes
    
    def get_full_command_line(self, pid: str) -> Optional[str]:
        """Get the full command line including all arguments for a process"""
        try:
            # Get full command line with all arguments
            result = subprocess.run(['ps', '-p', pid, '-o', 'args='],
                                  capture_output=True, text=True)
            return result.stdout.strip()
        except Exception as e:
            print(f"Error getting command line for PID {pid}: {e}", file=sys.stderr)
            return None

    def extract_command_name(self, full_command: str) -> Optional[str]:
        """Extract the actual command name from a full command line

        Examples:
        - "node /path/to/bin/codex --args" -> "codex"
        - "python /usr/local/bin/claude" -> "claude"
        - "/usr/bin/npm run dev" -> "npm"
        """
        if not full_command:
            return None

        # Split by spaces to get individual parts
        parts = full_command.split()
        if not parts:
            return None

        # Check each part for target commands
        for part in parts:
            # Extract basename from paths
            basename = os.path.basename(part)

            # Check if this basename matches any target command
            for target in self.target_commands:
                if target == basename or basename.startswith(target):
                    return basename

        return None

    def walk_process_tree(self, pid: str, depth: int = 0) -> Optional[str]:
        """Walk the process tree to find the actual command

        For shell processes, looks at children to find the actual running command.
        For node processes, extracts the command from the full command line.
        """
        if depth > 5:  # Prevent infinite recursion
            return None

        try:
            # Get the full command line of the current process
            full_cmd = self.get_full_command_line(pid)
            if not full_cmd:
                return None

            # Check if current process matches a target
            extracted = self.extract_command_name(full_cmd)
            if extracted:
                return full_cmd

            # Build process tree to find children
            ps_cmd = ['ps', '-eo', 'pid,ppid,command']
            result = subprocess.run(ps_cmd, capture_output=True, text=True)

            processes = {}
            for line in result.stdout.strip().split('\n')[1:]:  # Skip header
                if line.strip():
                    parts = line.split(None, 2)
                    if len(parts) >= 3:
                        pid_val = parts[0]
                        ppid_val = parts[1]
                        command = parts[2]
                        processes[pid_val] = {'ppid': ppid_val, 'command': command}

            # Find children of the given PID
            children = []
            for child_pid, info in processes.items():
                if info['ppid'] == pid:
                    children.append(child_pid)

            # Check children recursively
            for child_pid in children:
                child_cmd = self.walk_process_tree(child_pid, depth + 1)
                if child_cmd:
                    return child_cmd

        except Exception as e:
            if depth == 0:  # Only print error at top level
                print(f"Error walking process tree for PID {pid}: {e}", file=sys.stderr)

        return None
    
    def get_pane_content(self, session: str, window: str, pane: str) -> str:
        """Get the current content/output of a pane"""
        try:
            cmd = ['tmux', 'capture-pane', '-p', '-t', f'{session}:{window}.{pane}']
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.stdout.strip()
        except Exception as e:
            return f"Error getting pane content: {e}"
    
    def detect_commands(self) -> List[TmuxProcess]:
        """Detect all running target commands in tmux sessions"""
        panes = self.get_tmux_panes()
        detected = []

        # Commands that are just wrappers/shells - always walk their tree
        wrapper_commands = ['zsh', 'bash', 'sh', 'fish', 'node', 'python', 'python3', 'ruby', 'perl']

        for pane in panes:
            # If it's a wrapper command, walk the process tree to find the actual command
            if pane.current_command in wrapper_commands:
                actual_cmd = self.walk_process_tree(pane.pane_pid)
                if actual_cmd:
                    # Check if the actual command contains any target
                    if any(target in actual_cmd for target in self.target_commands):
                        pane.actual_command = actual_cmd
                        detected.append(pane)
                    # Also check if just the wrapper was a target (e.g., want to find all node processes)
                    elif pane.current_command in self.target_commands:
                        pane.actual_command = actual_cmd or pane.current_command
                        detected.append(pane)
            # For non-wrapper commands, check if they directly match targets
            elif any(target in pane.current_command for target in self.target_commands):
                pane.actual_command = pane.current_command
                detected.append(pane)

        return detected
    
    def switch_to_pane(self, session: str, window: str, pane: str):
        """Switch to a specific tmux pane"""
        try:
            # Attach to session and select window/pane
            subprocess.run(['tmux', 'switch-client', '-t', session], check=True)
            subprocess.run(['tmux', 'select-window', '-t', f'{session}:{window}'], check=True)
            subprocess.run(['tmux', 'select-pane', '-t', f'{session}:{window}.{pane}'], check=True)
            print(f"Switched to {session}:{window}.{pane}")
        except subprocess.CalledProcessError as e:
            print(f"Error switching to pane: {e}", file=sys.stderr)
    
    def format_for_fzf(self, processes: List[TmuxProcess]) -> str:
        """Format processes for fzf display"""
        lines = []
        for proc in processes:
            display_cmd = proc.actual_command or proc.current_command
            # Truncate long commands
            if len(display_cmd) > 50:
                display_cmd = display_cmd[:47] + "..."
            
            line = f"{proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}"
            lines.append(line)
        
        return '\n'.join(lines)
    
    def run_fzf_interface(self, processes: List[TmuxProcess]) -> Optional[TmuxProcess]:
        """Run fzf interface to select a process"""
        if not processes:
            print("No target commands found running in tmux sessions.")
            return None

        # Create mapping between display lines and processes
        process_map = {}
        display_lines = []

        for proc in processes:
            display_cmd = proc.actual_command or proc.current_command
            # Truncate long commands for display
            if len(display_cmd) > 80:
                display_cmd = display_cmd[:77] + "..."

            # Format: session:window.pane | command
            line = f"{proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}"
            process_map[line] = proc
            display_lines.append(line)

        # Prepare fzf command with preview
        # The preview command extracts the target (first field) and captures that pane
        fzf_cmd = [
            'fzf',
            '--ansi',
            '--prompt=Select tmux pane> ',
            '--header=Use arrows to navigate, Enter to switch, Esc to cancel',
            '--preview', 'tmux capture-pane -e -p -t {1}',
            '--preview-window=right:60%:wrap',
            '--height=90%',
            '--border=rounded',
            '--info=inline',
            '--layout=reverse'
        ]

        try:
            # Run fzf
            proc = subprocess.Popen(
                fzf_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            input_text = '\n'.join(display_lines)
            stdout, stderr = proc.communicate(input=input_text)

            if proc.returncode == 0 and stdout.strip():
                selected_line = stdout.strip()
                return process_map.get(selected_line)

        except FileNotFoundError:
            print("fzf not found. Please install fzf to use the interactive interface.", file=sys.stderr)
            print("Install with: brew install fzf (macOS) or apt install fzf (Linux)")
            # Fallback to simple selection
            print("\nAvailable commands:")
            for i, proc in enumerate(processes):
                display_cmd = proc.actual_command or proc.current_command
                print(f"{i+1}. {proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}")

            try:
                choice = int(input("\nSelect a process (number): ")) - 1
                if 0 <= choice < len(processes):
                    return processes[choice]
            except (ValueError, IndexError):
                pass

        return None


def main():
    parser = argparse.ArgumentParser(description='Find and manage running commands in tmux sessions')
    parser.add_argument('--commands', '-c', nargs='+', help='Target commands to look for')
    parser.add_argument('--list', '-l', action='store_true', help='List all detected commands')
    parser.add_argument('--json', '-j', action='store_true', help='Output in JSON format')
    
    args = parser.parse_args()
    
    finder = TmuxCommandFinder(args.commands)
    detected = finder.detect_commands()
    
    if args.list:
        if args.json:
            output = []
            for proc in detected:
                output.append({
                    'session': proc.session_name,
                    'window': proc.window_index,
                    'pane': proc.pane_index,
                    'current_command': proc.current_command,
                    'actual_command': proc.actual_command
                })
            print(json.dumps(output, indent=2))
        else:
            for proc in detected:
                display_cmd = proc.actual_command or proc.current_command
                print(f"{proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}")
        return
    
    # Run interactive interface
    selected = finder.run_fzf_interface(detected)
    
    if selected:
        finder.switch_to_pane(selected.session_name, selected.window_index, selected.pane_index)


if __name__ == '__main__':
    main()