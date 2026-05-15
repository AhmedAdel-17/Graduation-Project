import sys
import os
import json
from pprint import pprint
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.config import set_config
from langgraph.prebuilt import ToolNode

# Apply configuration
set_config(DEFAULT_CONFIG)
load_dotenv()

# Initialize LLM
llm = ChatOpenAI(
    model=DEFAULT_CONFIG["quick_think_llm"],
    base_url=DEFAULT_CONFIG["backend_url"],
    temperature=0
)

# Import analyst creators
from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst

# Import tools specifically for EGX
from tradingagents.agents.utils.fundamental_data_tools import get_egx_fundamentals, get_egx_income, get_egx_balance, get_egx_ratios
from tradingagents.agents.utils.agent_utils import get_stock_data, get_indicators, get_news
from tradingagents.agents.utils.news_data_tools import get_egx_company_news, get_egx_market_news

def test_analyst(analyst_name, node_creator, tool_funcs, message_key, report_key, extra_key=None):
    print("=" * 80)
    print(f"TESTING ANALYST: {analyst_name.upper()}")
    print("=" * 80)
    
    node = node_creator(llm)
    tools_node = ToolNode(tool_funcs)
    
    state = {
        "trade_date": "2025-01-17",
        "company_of_interest": "COMI.CA",
        "target_market": "EGX",
        "trading_currency": "EGP",
        message_key: []
    }
    
    print("\n[1] INITIAL STATE (Input to Agent):")
    pprint({k: v for k, v in state.items() if k != message_key})
    
    print("\n[2] RUNNING AGENT (Iteration 1)...")
    result = node(state)
    
    iteration = 1
    max_iterations = 12
    
    while iteration <= max_iterations:
        if message_key in result and len(result[message_key]) > 0:
            latest_msg = result[message_key][-1]
            
            if isinstance(latest_msg, AIMessage) and latest_msg.tool_calls:
                print(f"\n[{iteration}] AGENT REPLIED WITH TOOL CALLS:")
                for tc in latest_msg.tool_calls:
                    print(f"  - Tool: {tc['name']} | Args: {tc['args']}")
                    
                print(f"\n[{iteration}] EXECUTING TOOLS (manually)...")
                tool_result_messages = []
                for tc in latest_msg.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]
                    tool_func = next((t for t in tool_funcs if t.name == tool_name), None)
                    if tool_func:
                        try:
                            res = tool_func.invoke(tool_args)
                            tool_result_messages.append(ToolMessage(content=str(res), name=tool_name, tool_call_id=tc["id"]))
                        except Exception as e:
                            print(f"  - Error running {tool_name}: {e}")
                            tool_result_messages.append(ToolMessage(content=f"Error: {e}", name=tool_name, tool_call_id=tc["id"]))
                    else:
                        print(f"  - Tool {tool_name} not found!")
                        tool_result_messages.append(ToolMessage(content=f"Tool {tool_name} not found", name=tool_name, tool_call_id=tc["id"]))

                if not isinstance(state[message_key], list):
                    state[message_key] = []
                if len(state[message_key]) == 0:
                     state[message_key].append(HumanMessage(content=state["company_of_interest"]))
                     
                state[message_key].extend([latest_msg] + tool_result_messages)
                
                iteration += 1
                if iteration > max_iterations:
                    print(f"\n[WARNING] MAX ITERATIONS ({max_iterations}) REACHED. Breaking loop.")
                    break
                
                print(f"\n[{iteration}] RUNNING AGENT (Iteration {iteration})...")
                result = node(state)
            else:
                # No more tool calls, we reached the end
                break
        else:
            print("\nWARNING: Agent returned unexpected format.")
            pprint(result)
            break
            
    # Final extraction and file saving
    report_content = f"# ISOLATED TEST REPORT: {analyst_name.upper()}\n\n"
    
    if isinstance(result.get(message_key), list) and len(result[message_key]) > 0:
        latest_msg = result[message_key][-1]
        print(f"\n[FINAL] AGENT STOPPED CALLING TOOLS. Output Content length: {len(latest_msg.content) if isinstance(latest_msg, AIMessage) else 'N/A'}")
        
    print("\n[FINAL OUTPUT DATA]")
    if report_key in result and result[report_key]:
        print(f"\n--- {report_key} ---")
        res_str = str(result[report_key])
        print(res_str.encode('ascii', 'replace').decode('ascii')[:1000] + "\n... [TRUNCATED] ...")
        report_content += f"## {report_key}\n\n{res_str}\n\n"
    else:
        print(f"\n--- {report_key} is missing or empty! ---")
        report_content += f"## {report_key}\n\n[MISSING OR EMPTY]\n\n"
        
    if extra_key and extra_key in result and result[extra_key]:
        print(f"\n--- {extra_key} (Structured JSON) ---")
        json_str = json.dumps(result[extra_key], indent=2)
        print(json_str)
        report_content += f"## {extra_key}\n\n```json\n{json_str}\n```\n\n"
        
    # Build file path
    import os
    comp = state.get("company_of_interest", "UNKNOWN").replace(".CA", "")
    out_dir = f"eval_results/{comp}/TradingAgentsStrategy_logs"
    os.makedirs(out_dir, exist_ok=True)
    out_file = f"{out_dir}/test_isolated_{analyst_name}.md"
    
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report_content)
        
    print(f"\nReport written to: {out_file}")
    print("\nDone testing", analyst_name, "\n")


if __name__ == "__main__":
    from tradingagents.agents.utils.social_media_tools import get_social_sentiment, get_social_media_posts
    analyst_choice = sys.argv[1] if len(sys.argv) > 1 else "fundamentals"
    
    if analyst_choice == "fundamentals":
        tools = [get_egx_fundamentals, get_egx_income, get_egx_balance, get_egx_ratios]
        test_analyst("Fundamentals", create_fundamentals_analyst, tools, "fundamentals_messages", "fundamentals_report", "fundamental_analysis")
    elif analyst_choice == "market":
        tools = [get_stock_data, get_indicators]
        test_analyst("Market", create_market_analyst, tools, "market_messages", "market_report", "technical_analysis")
    elif analyst_choice == "news":
        tools = [get_egx_company_news, get_egx_market_news]
        test_analyst("News", create_news_analyst, tools, "news_messages", "news_report", "sentiment_analysis")
    elif analyst_choice == "social":
        tools = [get_social_sentiment, get_social_media_posts]
        test_analyst("Social", create_social_media_analyst, tools, "social_messages", "social_report", "social_sentiment_analysis")
    elif analyst_choice == "all":
        tools1 = [get_egx_fundamentals, get_egx_income, get_egx_balance, get_egx_ratios]
        test_analyst("Fundamentals", create_fundamentals_analyst, tools1, "fundamentals_messages", "fundamentals_report", "fundamental_analysis")
        
        tools2 = [get_stock_data, get_indicators]
        test_analyst("Market", create_market_analyst, tools2, "market_messages", "market_report", "technical_analysis")
        
        tools3 = [get_egx_company_news, get_egx_market_news]
        test_analyst("News", create_news_analyst, tools3, "news_messages", "news_report", "sentiment_analysis")
        
        tools4 = [get_social_sentiment, get_social_media_posts]
        test_analyst("Social", create_social_media_analyst, tools4, "social_messages", "sentiment_report", "social_sentiment_analysis")
    else:
        print(f"Unknown analyst choice: {analyst_choice}")
