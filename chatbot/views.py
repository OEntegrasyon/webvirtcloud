from django.http import StreamingHttpResponse
from instances.models import Instance
import json
import requests

import torch
import numpy as np
import faiss
from transformers import AutoTokenizer, AutoModel

model_name = "sentence-transformers/all-MiniLM-L6-v2"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModel.from_pretrained(model_name)

documents = [
    "WebVirtCloud is a web-based control panel designed for managing KVM virtual machines using a modern and intuitive interface. When users first visit the site, they are presented with a login page where they can enter their username and password to access the system.",
    "After logging in, users are directed to the dashboard (Instances page), which provides a quick overview of all virtual machines (instances), including details such as the VM owner, status, vCPU, memory, and a set of action buttons for each instance. These actions include Power On, Suspend, Power Off, Power Cycle, and View Console. At the top of Instances page, there is a search input that allows users to search for virtual machines by name. New instances can be added using the green 'Add' button at the top of the page.",
    "On the left side of the top navigation bar, there are two main sections: Instances and Computes. On the right side, there is a settings icon; when focused or clicked, it reveals a dropdown menu that includes Bulk Operations, Failover, Users, Groups, Logs, and Settings. Beside the settings icon is the username, which when clicked allows users to manage their profile or log out.",
    "When an instance is selected, the user is taken to the instance details page, where they can view and manage specific information about that virtual machine. At the top of this page is the name of the instance, followed by key details such as status, CPU, RAM, and disk usage. Below these, there are seven buttons for managing the VM: Power; to power on or off the instance, Access; to access the console, Resize; to adjust CPU, RAM, and disk size, Snapshot; to take and manage snapshots, Settings; to configure options like Boot, Disk, Console, Network, Clone, Migrate, Failover, XML, and user access, Stats; to view real-time CPU usage, memory usage, bandwidth, disk I/O, and logs. Destroy; to delete the instance.",
    "At the top of the Computes section, there is a search input to find computes by name. Next to it is a compute type selector, where users can click to add a new compute. Below this, a list displays all existing computes, showing their name, status, and details, along with action buttons for Overview, Edit, and Delete. When the Overview button for a compute is clicked, a detailed management page opens. At the top of this page is the compute's name, followed by several section buttons. When a section is selected, its details are shown directly below. The available sections include: Overview; displays basic information and performance metrics, Instances; shows all VMs on the compute and allows new instances to be added via a green 'Add' button, Storages; displays storage information and lets users add a new virtual disk store, Networks; shows network configurations, Interfaces, NWFilters, and Secrets; provide additional advanced management options.",
    "The Bulk Operations section, accessible from the settings menu in the top navigation bar, allows users to power on or power off multiple instances at once.",
    "The Failover section provides settings to configure failover behavior for individual instances.",
    "In the Users section, administrators can view details for existing users, such as status, last login time, and assigned permissions. New users can be added using the green 'Add' button at the top of the page. Each user entry includes action buttons for Overview, Edit, Deactivate, or Delete.",
    "The Groups section allows administrators to manage user groups and their associated permissions.",
    "The Logs section displays a complete log of user activity within the system.",
    "The Settings section allows users to configure both general application settings (such as language, theme SASS path, and UI theme) and advanced options related to VM behavior. These include options for instance display, console settings, disk configurations, logging, quotas, access management, VM hardware types (CPU, NIC, video), and many more.",
]

def encode_texts(texts):
    encoded_input = tokenizer(
        texts,
        padding=True,
        truncation=True,
        return_tensors='pt'
    )

    with torch.no_grad():
        model_output = model(**encoded_input)

    attention_mask = encoded_input['attention_mask']
    token_embeddings = model_output.last_hidden_state
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()

    sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
    sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
    embeddings = sum_embeddings / sum_mask

    embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
    return embeddings.cpu().numpy()

doc_vectors = encode_texts(documents)
index = faiss.IndexFlatIP(doc_vectors.shape[1])
index.add(doc_vectors)

def retrieve_context(docs, query, top_k=2):
    query_vector = encode_texts([query])
    _, indices = index.search(np.array(query_vector), k=top_k)
    return [docs[i] for i in indices[0]]


def stream_chat(request):
    prompt = request.GET.get('prompt', 'Hello')
    user_instances = [name['name'] for name in list(Instance.objects.values())]

    
    extra_context = [
        f"my name/user name is {request.user}.",
        "an instance is a virtual machine (vm)"
    ]

    context_docs = retrieve_context(documents + extra_context, prompt)
    context = "\n\n".join(context_docs) + f"\n\nYou are an AI assistant for WebVirtCloud and your name is ZekAI. You are here to assist WebVirtCloud users. You should only answer about WebVirtCloud and KVM. As the user {request.user}, I have these instances (VM): {user_instances}"

    response = StreamingHttpResponse(llama3(prompt, context), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    return response

def llama3(prompt, context):
    url = "http://localhost:11434/api/chat"
    data = {
        "model": "hf.co/OzgurEnt/OZGURLUK-GPT-LinuxGeneral:Q8_0",
        "messages": [
            {"role": "system", "content": f"Use this context:\n{context}"},
            {"role": "user", "content": prompt}
        ],
        "stream": True,
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, headers=headers, json=data, stream=True)
    except Exception:
        yield 'data: {"message": {"content": "Check if Ollama Service is Up and Running..."}, "done": "true"}\n\n'
        yield "data: [DONE]\n\n"
        return

    for line in response.iter_lines(decode_unicode=True):
        if line:
            data = json.loads(line)
            dt = data["message"]["content"]
            dt = dt.replace('"', "`").replace('**', "`").replace('\n', '<br>')
            yield f'data: {json.dumps({"message": {"content": dt}, "done": str(data["done"])})}\n\n'

    yield "data: [DONE]\n\n"
