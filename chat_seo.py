# chat_seo.py — Chat interactif avec l'agent SEO
import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from analyseur import analyser_page
from sitemap import auditer_site

load_dotenv()

llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),  
    azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
    api_version="2024-10-21",
)

# --- Les 2 outils de l'agent ---
@tool
def auditer_une_page(url: str) -> dict:
    """Analyse le SEO d'UNE page web précise à partir de son URL."""
    return analyser_page(url)

@tool
def auditer_tout_le_site(sitemap_url: str) -> list:
    """Analyse le SEO de TOUTES les pages d'un site via l'URL de son sitemap."""
    return auditer_site(sitemap_url, max_pages=50)

outils = [auditer_une_page, auditer_tout_le_site]

# --- Instructions + mémoire de conversation ---
prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es un expert SEO qui aide l'utilisateur à auditer et améliorer son site. "
     "Utilise tes outils pour récupérer les données réelles, puis réponds de façon "
     "claire et priorisée. Quand l'utilisateur demande des corrections pour une page, "
     "donne des recommandations concrètes et actionnables."),
    MessagesPlaceholder("chat_history"),   # la mémoire !
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

agent = create_tool_calling_agent(llm, outils, prompt)
executeur = AgentExecutor(agent=agent, tools=outils, verbose=False)  # mets True pour voir sa réflexion

# --- La boucle de chat ---
print("🔍 Agent SEO prêt ! (tape 'quit' pour sortir)\n")
print("Exemples : 'Audite explorawatersports.com, sitemap: https://explorawatersports.com/sitemap.xml'")
print("           'Quelles pages ont des images sans alt ?'")
print("           'Donne-moi les corrections pour la page /news/'\n")

historique = []
while True:
    question = input("Toi > ")
    if question.lower() in ("quit", "exit", "q"):
        print("À bientôt ! 👋")
        break
    if not question.strip():
        continue

    reponse = executeur.invoke({"input": question, "chat_history": historique})
    print(f"\n🤖 Agent > {reponse['output']}\n")

    # On garde la mémoire de l'échange
    historique.append(HumanMessage(content=question))
    historique.append(AIMessage(content=reponse["output"]))