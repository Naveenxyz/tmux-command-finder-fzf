#!/usr/bin/env python3
"""
Tmux Command Finder - A tool to detect and manage running commands in tmux sessions

Architecture Overview:
---------------------
This tool consists of three main components:

1. Discovery Layer (get_tmux_panes, get_full_command_line):
   - Queries tmux for all active panes and their PIDs
   - Retrieves full command-line arguments for each process

2. Analysis Layer (extract_command_name, walk_process_tree, detect_commands):
   - Parses command lines to identify actual commands (e.g., "node /bin/codex" -> "codex")
   - Walks process trees to find real commands behind shells/interpreters
   - Filters results to match target commands

3. Interface Layer (run_fzf_interface, switch_to_pane, kill_pane):
   - Presents results through fzf with live preview
   - Handles user actions (switching, killing panes)

Flow:
-----
tmux panes -> get PIDs -> parse command lines -> walk process tree ->
filter targets -> display in fzf -> switch/kill pane

Example:
--------
Shell pane (PID 1234) running "bash"
  └─> Child process (PID 1235) running "node /usr/local/bin/codex serve"

Result: Detects "codex" as the target command and shows full command line
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
    """Represents a tmux pane and its associated process information.

    Attributes:
        session_name: Name of the tmux session (e.g., "main")
        window_index: Window number within the session (e.g., "0")
        pane_index: Pane number within the window (e.g., "1")
        pane_pid: Process ID of the pane's shell process
        current_command: Command reported by tmux (often just "bash", "zsh", etc.)
        actual_command: Actual command detected via process tree walking (e.g., "codex serve")
        full_command: Full command line with all arguments (deprecated, use actual_command)

    Example:
        >>> proc = TmuxProcess(
        ...     session_name="dev",
        ...     window_index="2",
        ...     pane_index="0",
        ...     pane_pid="12345",
        ...     current_command="bash",
        ...     actual_command="node /usr/local/bin/codex serve"
        ... )
    """
    session_name: str
    window_index: str
    pane_index: str
    pane_pid: str
    current_command: str
    actual_command: Optional[str] = None
    full_command: Optional[str] = None


class TmuxCommandFinder:
    """Main class for discovering and managing commands in tmux sessions.

    This class provides methods to:
    - Discover all tmux panes and their processes
    - Walk process trees to find actual commands behind shells
    - Filter processes by target command names
    - Present an interactive fzf interface for selection
    - Switch to or kill selected panes

    Example:
        >>> finder = TmuxCommandFinder(['npm', 'python', 'docker'])
        >>> detected = finder.detect_commands()
        >>> selected = finder.run_fzf_interface(detected)
        >>> if selected:
        ...     finder.switch_to_pane(selected.session_name,
        ...                           selected.window_index,
        ...                           selected.pane_index)
    """

    def __init__(self, target_commands: Optional[List[str]] = None):
        """Initialize the finder with target commands to search for.

        Args:
            target_commands: List of command names to search for. If None,
                uses a default list of common development tools.

        Default commands include:
            - Dev tools: codex, claude, opencode
            - Package managers: npm, yarn
            - Languages: python, node
            - Build tools: cargo, go, java, mvn, gradle
            - DevOps: docker, kubectl
        """
        self.target_commands = target_commands or [
            'codex', 'claude', 'opencode', 'npm', 'yarn', 'python', 'node',
            'cargo', 'go', 'java', 'mvn', 'gradle', 'docker', 'kubectl'
        ]
    
    def get_tmux_panes(self) -> List[TmuxProcess]:
        """Query tmux for all active panes across all sessions.

        Uses tmux's list-panes command with format strings to extract:
        - Session name, window index, pane index
        - Current command (as reported by tmux, e.g., "bash", "zsh")
        - Process ID of the pane's shell

        Returns:
            List of TmuxProcess objects, one per pane. Empty list if tmux
            is not running or no panes exist.

        Example output format from tmux:
            "main:0:1:bash:12345"
            "dev:2:0:zsh:12346"
        """
        # Query tmux for all panes with formatted output
        cmd = ['tmux', 'list-panes', '-a', '-F', '#{session_name}:#{window_index}:#{pane_index}:#{pane_current_command}:#{pane_pid}']
        result = subprocess.run(cmd, capture_output=True, text=True)

        processes = []
        for line in result.stdout.strip().split('\n'):
            if line:
                # Parse colon-separated values
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
        """Retrieve the complete command line for a given process ID.

        Uses ps to get the full command including all arguments and flags.
        This is more detailed than tmux's pane_current_command.

        Args:
            pid: Process ID as a string (e.g., "12345")

        Returns:
            Full command line string, or None if the process doesn't exist
            or there's an error querying it.

        Example return values:
            "node /usr/local/bin/codex serve --port 3000"
            "python3 /home/user/.local/bin/claude code"
            "/usr/bin/npm run dev"
        """
        try:
            # Use ps to get full command line with all arguments
            result = subprocess.run(['ps', '-p', pid, '-o', 'args='],
                                  capture_output=True, text=True)
            return result.stdout.strip()
        except Exception as e:
            print(f"Error getting command line for PID {pid}: {e}", file=sys.stderr)
            return None

    def extract_command_name(self, full_command: str) -> Optional[str]:
        """Extract the actual command name from a full command line.

        Many commands run through interpreters or wrappers. This method
        parses the command line to find the actual target command.

        Algorithm:
            1. Split command line by spaces
            2. For each part, extract the basename (filename without path)
            3. Check if basename matches or starts with any target command
            4. Return first match found

        Args:
            full_command: Complete command line string from ps output

        Returns:
            The extracted command name if found, None otherwise

        Examples:
            >>> finder = TmuxCommandFinder(['codex', 'claude', 'npm'])
            >>> finder.extract_command_name("node /usr/local/bin/codex serve")
            'codex'
            >>> finder.extract_command_name("python3 /home/user/.local/bin/claude")
            'claude'
            >>> finder.extract_command_name("/usr/bin/npm run dev")
            'npm'
            >>> finder.extract_command_name("bash -c 'echo test'")
            None
        """
        if not full_command:
            return None

        # Split by spaces to get individual arguments
        parts = full_command.split()
        if not parts:
            return None

        # Check each part for target commands
        # This handles cases like: "node /path/to/bin/codex --args"
        for part in parts:
            # Extract basename to handle full paths
            # e.g., "/usr/local/bin/codex" -> "codex"
            basename = os.path.basename(part)

            # Check if this basename matches any target command
            for target in self.target_commands:
                # Exact match or starts with target (handles versioned commands like "python3")
                if target == basename or basename.startswith(target):
                    return basename

        return None

    def walk_process_tree(self, pid: str, depth: int = 0) -> Optional[str]:
        """Walk the process tree to find the actual running command.

        Shell processes (bash, zsh, etc.) often don't directly run target
        commands. Instead, they spawn child processes. This method recursively
        walks the process tree to find the actual target command.

        Algorithm:
            1. Get full command line for current PID
            2. Check if it matches a target command
            3. If not, query all system processes to build parent-child map
            4. Find all children of current PID
            5. Recursively check each child (depth-first search)
            6. Return first match found

        Args:
            pid: Process ID to start searching from
            depth: Current recursion depth (used to prevent infinite loops)

        Returns:
            Full command line of the first matching command found, or None

        Example Process Tree:
            bash (PID 1000) -> not a target
              └─> node (PID 1001) -> not directly matching
                  └─> codex (PID 1002) -> MATCH! Return full command line

        Note:
            - Max recursion depth is 5 to prevent infinite loops
            - Only prints errors at the top level (depth 0) to reduce noise
        """
        # Recursion safety: prevent infinite loops in unusual process trees
        if depth > 5:  # Max depth limit
            return None

        try:
            # Get the full command line of the current process
            full_cmd = self.get_full_command_line(pid)
            if not full_cmd:
                return None

            # Check if current process matches a target command
            extracted = self.extract_command_name(full_cmd)
            if extracted:
                # Found a match! Return the full command line
                return full_cmd

            # No match at this level, build process tree to find children
            # Query all processes with PID, parent PID (PPID), and command
            ps_cmd = ['ps', '-eo', 'pid,ppid,command']
            result = subprocess.run(ps_cmd, capture_output=True, text=True)

            # Parse ps output into a dictionary: {pid: {ppid, command}}
            processes = {}
            for line in result.stdout.strip().split('\n')[1:]:  # Skip header row
                if line.strip():
                    # Split into max 3 parts: PID, PPID, and rest is command
                    parts = line.split(None, 2)
                    if len(parts) >= 3:
                        pid_val = parts[0]
                        ppid_val = parts[1]
                        command = parts[2]
                        processes[pid_val] = {'ppid': ppid_val, 'command': command}

            # Find all children of the given PID
            children = []
            for child_pid, info in processes.items():
                if info['ppid'] == pid:  # This process's parent is our target PID
                    children.append(child_pid)

            # Recursively check children (depth-first search)
            for child_pid in children:
                child_cmd = self.walk_process_tree(child_pid, depth + 1)
                if child_cmd:
                    # Found a match in a child process
                    return child_cmd

        except Exception as e:
            # Only print error at top level to avoid spam during recursion
            if depth == 0:
                print(f"Error walking process tree for PID {pid}: {e}", file=sys.stderr)

        # No match found in this branch
        return None
    
    def get_pane_content(self, session: str, window: str, pane: str) -> str:
        """Get the current visible content of a tmux pane.

        Captures the pane's visible buffer, which is useful for previewing
        what's currently displayed in the pane.

        Args:
            session: Session name (e.g., "main")
            window: Window index (e.g., "0")
            pane: Pane index (e.g., "1")

        Returns:
            String containing the pane's visible content, or error message
        """
        try:
            cmd = ['tmux', 'capture-pane', '-p', '-t', f'{session}:{window}.{pane}']
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.stdout.strip()
        except Exception as e:
            return f"Error getting pane content: {e}"
    
    def detect_commands(self) -> List[TmuxProcess]:
        """Detect all running target commands across all tmux sessions.

        This is the main detection algorithm that combines pane discovery
        with process tree walking to find target commands.

        Algorithm:
            1. Get all tmux panes
            2. For each pane:
               - If it's a shell/wrapper, walk the process tree to find children
               - If it directly matches a target, add it to results
            3. Return all detected processes

        Returns:
            List of TmuxProcess objects with actual_command populated

        Example:
            If target_commands = ['npm', 'codex']:
            - Pane running "bash" -> walks tree -> finds "npm run dev" -> detected
            - Pane running "codex" directly -> detected
            - Pane running "vim" -> not detected
        """
        panes = self.get_tmux_panes()
        detected = []

        # Commands that are just wrappers/shells - always walk their process tree
        # These typically don't do meaningful work themselves, but spawn child processes
        wrapper_commands = ['zsh', 'bash', 'sh', 'fish', 'node', 'python', 'python3', 'ruby', 'perl']

        for pane in panes:
            # Case 1: Shell/wrapper command - need to look at children
            if pane.current_command in wrapper_commands:
                # Walk the process tree to find what's actually running
                actual_cmd = self.walk_process_tree(pane.pane_pid)
                if actual_cmd:
                    # Check if the actual command contains any target
                    if any(target in actual_cmd for target in self.target_commands):
                        pane.actual_command = actual_cmd
                        detected.append(pane)
                    # Edge case: the wrapper itself is a target (e.g., finding all python processes)
                    elif pane.current_command in self.target_commands:
                        pane.actual_command = actual_cmd or pane.current_command
                        detected.append(pane)

            # Case 2: Direct match - pane's command is already a target
            elif any(target in pane.current_command for target in self.target_commands):
                pane.actual_command = pane.current_command
                detected.append(pane)

        return detected
    
    def switch_to_pane(self, session: str, window: str, pane: str):
        """Switch the tmux client to a specific pane.

        Executes three tmux commands in sequence:
        1. Switch to the target session
        2. Select the target window within that session
        3. Select the target pane within that window

        Args:
            session: Session name (e.g., "main")
            window: Window index (e.g., "0")
            pane: Pane index (e.g., "1")

        Side Effects:
            Changes the current tmux client's active session/window/pane
        """
        try:
            # Step 1: Switch to the target session
            subprocess.run(['tmux', 'switch-client', '-t', session], check=True)
            # Step 2: Select the target window
            subprocess.run(['tmux', 'select-window', '-t', f'{session}:{window}'], check=True)
            # Step 3: Select the target pane
            subprocess.run(['tmux', 'select-pane', '-t', f'{session}:{window}.{pane}'], check=True)
            print(f"Switched to {session}:{window}.{pane}")
        except subprocess.CalledProcessError as e:
            print(f"Error switching to pane: {e}", file=sys.stderr)

    def kill_pane(self, session: str, window: str, pane: str):
        """Terminate a specific tmux pane and its processes.

        WARNING: This will kill the pane and all processes running in it.

        Args:
            session: Session name (e.g., "main")
            window: Window index (e.g., "0")
            pane: Pane index (e.g., "1")

        Side Effects:
            - Destroys the tmux pane
            - Terminates all processes in that pane
        """
        try:
            subprocess.run(['tmux', 'kill-pane', '-t', f'{session}:{window}.{pane}'], check=True)
            print(f"Killed pane {session}:{window}.{pane}")
        except subprocess.CalledProcessError as e:
            print(f"Error killing pane: {e}", file=sys.stderr)
    
    def format_for_fzf(self, processes: List[TmuxProcess]) -> str:
        """Format processes into lines suitable for fzf display.

        Each line format: "session:window.pane | command"

        Args:
            processes: List of detected TmuxProcess objects

        Returns:
            Newline-separated string of formatted process lines

        Example output:
            "main:0.1 | node /usr/local/bin/codex serve
             dev:2.0 | npm run dev
             work:1.3 | python manage.py runserver"
        """
        lines = []
        for proc in processes:
            display_cmd = proc.actual_command or proc.current_command
            # Truncate long commands to keep display clean
            if len(display_cmd) > 50:
                display_cmd = display_cmd[:47] + "..."

            # Format: "session:window.pane | command"
            line = f"{proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}"
            lines.append(line)

        return '\n'.join(lines)
    
    def run_fzf_interface(self, processes: List[TmuxProcess]) -> Optional[TmuxProcess]:
        """Present an interactive fzf interface for selecting a process.

        Features:
        - Fuzzy search across all detected commands
        - Live preview of pane content on the right
        - Enter: switch to selected pane
        - Ctrl-x: kill selected pane and refresh list
        - Esc: cancel

        Args:
            processes: List of detected TmuxProcess objects

        Returns:
            Selected TmuxProcess if user makes a selection, None if canceled
            or fzf is not installed

        Note:
            Falls back to simple numbered selection if fzf is not installed
        """
        if not processes:
            print("No target commands found running in tmux sessions.")
            return None

        # Create mapping between display lines and process objects
        # This allows us to map fzf's selection back to the TmuxProcess
        process_map = {}
        display_lines = []

        for proc in processes:
            display_cmd = proc.actual_command or proc.current_command
            # Truncate long commands for cleaner display
            if len(display_cmd) > 80:
                display_cmd = display_cmd[:77] + "..."

            # Format: "session:window.pane | command"
            line = f"{proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}"
            process_map[line] = proc
            display_lines.append(line)

        # Configure fzf with preview and keybindings
        # Preview: {1} refers to the first field (session:window.pane)
        # Keybinding: Ctrl-x kills the pane and reloads the list
        script_dir = os.path.dirname(os.path.abspath(__file__))
        kill_script = os.path.join(script_dir, 'tmux_command_finder.py')

        fzf_cmd = [
            'fzf',
            '--ansi',  # Enable ANSI color codes
            '--prompt=Select tmux pane> ',
            '--header=Enter: switch | Ctrl-x: kill pane | Esc: cancel',
            # Preview: Show live pane content using tmux capture-pane
            # {1} is fzf placeholder for first field (session:window.pane)
            '--preview', 'tmux capture-pane -e -p -t {1}',
            '--preview-window=right:60%:wrap',  # 60% width, wrapped text
            '--height=90%',
            '--border=rounded',
            '--info=inline',  # Show search info inline
            '--layout=reverse',  # Input at top
            # Ctrl-x binding: Kill pane silently, then reload the list
            '--bind', f'ctrl-x:execute-silent(python3 {kill_script} --kill {{1}})+reload(python3 {kill_script} --list)'
        ]

        try:
            # Launch fzf with our formatted process list
            proc = subprocess.Popen(
                fzf_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Send all display lines to fzf via stdin
            input_text = '\n'.join(display_lines)
            stdout, stderr = proc.communicate(input=input_text)

            # Check if user made a selection (returncode 0 = selection, 130 = canceled)
            if proc.returncode == 0 and stdout.strip():
                selected_line = stdout.strip()
                # Map the selected line back to the TmuxProcess object
                return process_map.get(selected_line)

        except FileNotFoundError:
            # fzf not installed - fall back to simple numbered selection
            print("fzf not found. Please install fzf to use the interactive interface.", file=sys.stderr)
            print("Install with: brew install fzf (macOS) or apt install fzf (Linux)")

            # Fallback: Simple numbered list
            print("\nAvailable commands:")
            for i, proc in enumerate(processes):
                display_cmd = proc.actual_command or proc.current_command
                print(f"{i+1}. {proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}")

            try:
                choice = int(input("\nSelect a process (number): ")) - 1
                if 0 <= choice < len(processes):
                    return processes[choice]
            except (ValueError, IndexError):
                # Invalid input, return None
                pass

        return None


def main():
    """Main entry point for the command-line interface.

    Supports three modes:
    1. Interactive mode (default): Launch fzf interface for selection
    2. List mode (--list): Print detected commands to stdout
    3. Kill mode (--kill): Kill a specific pane by target string

    Command-line arguments:
        --commands, -c: Specify custom target commands (overrides defaults)
        --list, -l: List mode - print all detected commands
        --json, -j: With --list, output in JSON format
        --kill, -k: Kill a specific pane (format: session:window.pane)
    """
    parser = argparse.ArgumentParser(description='Find and manage running commands in tmux sessions')
    parser.add_argument('--commands', '-c', nargs='+', help='Target commands to look for')
    parser.add_argument('--list', '-l', action='store_true', help='List all detected commands')
    parser.add_argument('--json', '-j', action='store_true', help='Output in JSON format')
    parser.add_argument('--kill', '-k', type=str, help='Kill pane by target (session:window.pane)')

    args = parser.parse_args()

    finder = TmuxCommandFinder(args.commands)

    # Mode 1: Kill a specific pane
    if args.kill:
        # Parse session:window.pane format (e.g., "main:0.1")
        try:
            parts = args.kill.split(':')
            if len(parts) == 2:
                session = parts[0]
                window_pane = parts[1].split('.')
                if len(window_pane) == 2:
                    window = window_pane[0]
                    pane = window_pane[1]
                    finder.kill_pane(session, window, pane)
                    return
        except Exception as e:
            print(f"Error parsing kill target: {e}", file=sys.stderr)
        print("Invalid kill target format. Expected: session:window.pane", file=sys.stderr)
        return

    # Detect all matching commands across tmux sessions
    detected = finder.detect_commands()

    # Mode 2: List detected commands
    if args.list:
        if args.json:
            # JSON output for programmatic consumption
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
            # Human-readable list
            for proc in detected:
                display_cmd = proc.actual_command or proc.current_command
                print(f"{proc.session_name}:{proc.window_index}.{proc.pane_index} | {display_cmd}")
        return

    # Mode 3: Interactive selection with fzf (default)
    selected = finder.run_fzf_interface(detected)

    if selected:
        # User made a selection - switch to that pane
        finder.switch_to_pane(selected.session_name, selected.window_index, selected.pane_index)


if __name__ == '__main__':
    main()