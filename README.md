# BreadBoard - Agentic Coding System

A streamlined, modular implementation of an agentic coding system that can generate, modify, and debug code autonomously. Works with gVisor template containers that are autoscaled by Ray. 

## Quick Start

### Basic Usage

```bash
# Run a task with a specific config
python main.py agent_configs/test_enhanced_agent_v2.yaml -t "Create a fibonacci calculator in Python"

# Start interactive session
python main.py agent_configs/test_enhanced_agent_v2.yaml -i

# Use custom workspace
python main.py agent_configs/test_enhanced_agent_v2.yaml -w my_project -t "Build a web scraper"
```

### Configuration

Agent configurations are stored in the `agent_configs/` directory. Key configs:

- `test_enhanced_agent_v2.yaml` - Enhanced agent with multiple tool calling modes
- `test_opencode_gpt5_nano_c_fs.yaml` - Specialized for C filesystem tasks
- `test_simple_native.yaml` - Simple native tool calling setup

## Architecture

### Core Components

- **`agentic_coder_prototype/`** - Main agent implementation
  - `agent.py` - Simplified interface
  - `agent_llm_openai.py` - Core LLM agent logic
  - `provider_routing.py` - Multi-provider support
  - `provider_adapters.py` - Provider-specific adapters
  - `tool_calling/` - Tool calling system

- **`agent_configs/`** - Agent configuration files
- **`sandbox_v2.py`** - Secure execution environment
- **`lsp_manager_v2.py`** - Language server integration
- **`implementations/`** - Tool definitions and system prompts

### Key Features

- **Multi-Provider Support**: OpenAI, Anthropic, OpenRouter
- **Secure Sandboxing**: Isolated execution environments
- **LSP Integration**: Language server support for enhanced code intelligence
- **Flexible Tool Calling**: Multiple syntax formats and execution modes
- **Modular Design**: Clean separation of concerns

## Development

### Project Structure

```
├── main.py                     # CLI entry point
├── agentic_coder_prototype/    # Core agent system
├── agent_configs/              # Agent configurations
├── implementations/            # Tools and prompts
├── sandbox_v2.py              # Execution sandbox
├── lsp_manager_v2.py          # Language server manager
├── tests/                     # Test suite
└── misc/                      # Non-essential files
```

### Testing

```bash
# Run specific tests
python -m pytest tests/test_agent_session.py -v

# Run all tests
python -m pytest tests/ -v
```

### Configuration

Agent behavior is controlled through YAML configuration files. Key parameters:

- `model`: LLM model to use (e.g., "openrouter/openai/gpt-4")
- `max_iterations`: Maximum agent loop iterations
- `tool_prompt_mode`: Tool calling syntax preference
- `tools_file`: Path to tool definitions

## Examples

### Creating a Simple Calculator

```bash
python main.py agent_configs/test_enhanced_agent_v2.yaml -t "Create a calculator.py that can add, subtract, multiply, and divide two numbers with error handling"
```

### Building a Web API

```bash
python main.py agent_configs/test_enhanced_agent_v2.yaml -t "Create a FastAPI web service with endpoints for user management (create, read, update, delete users)"
```

### Interactive Development

```bash
python main.py agent_configs/test_enhanced_agent_v2.yaml -i
> Create a data processing script
> Add unit tests for the script  
> Fix any bugs found in testing
> exit
```

## License

This project is experimental research software. Use at your own risk.
