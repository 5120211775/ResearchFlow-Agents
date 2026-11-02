"""Manual configuration switch. Does not alter checkpoints or restart processes."""
import argparse
from configure import read_env, update

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['status', 'google', 'deepseek', 'embedding-google', 'embedding-openrouter', 'proxy-enable', 'budget-research'])
args = parser.parse_args()
env = read_env()
if args.action in ('google', 'deepseek'):
    values = {'LLM_MODE': 'real'}
    for role in ('PLANNER', 'EXECUTOR', 'CRITIC', 'SYNTHESIZER', 'CHAT'):
        values['MODEL_' + role] = (
            'custom:' + env.get('DEEPSEEK_MODEL', 'deepseek-flash') if args.action == 'deepseek'
            else 'google:gemini-2.5-' + ('pro' if role in ('PLANNER', 'SYNTHESIZER') else 'flash'))
    if args.action == 'deepseek':
        values.update(CUSTOM_BASE_URL='https://api.deepseek.com/v1', CUSTOM_API_KEY=env['DEEPSEEK_API_KEY'])
    update(values)
elif args.action == 'embedding-google':
    update({'EMBEDDINGS_PROVIDER': 'google', 'EMBEDDINGS_MODEL': 'gemini-embedding-001'})
elif args.action == 'embedding-openrouter':
    update({'EMBEDDINGS_PROVIDER': 'openrouter', 'EMBEDDINGS_MODEL': 'openai/text-embedding-3-small'})
elif args.action == 'proxy-enable':
    update({'RESEARCH_API_PROXY': 'http://host.docker.internal:7897'})
elif args.action == 'budget-research':
    update({'MAX_INPUT_TOKENS': '1000000'})
env = read_env()
for key in ('LLM_MODE', 'MODEL_PLANNER', 'MODEL_EXECUTOR', 'MODEL_CRITIC', 'MODEL_SYNTHESIZER', 'MODEL_CHAT', 'CUSTOM_BASE_URL', 'EMBEDDINGS_PROVIDER', 'EMBEDDINGS_MODEL', 'RESEARCH_API_PROXY', 'MAX_PARALLEL_TASKS', 'MAX_CRITIC_LOOPS', 'MAX_INPUT_TOKENS', 'MAX_COST_PER_SESSION_USD', 'MAX_WALLCLOCK_SECONDS'):
    print(f'{key}={env.get(key, "")}')
print('Configuration affects new processes/new runs. Existing checkpoints were not modified.')
