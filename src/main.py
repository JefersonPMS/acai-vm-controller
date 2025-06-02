"""
FastAPI Controller para gerenciar VMs do Google Cloud Platform
Permite ligar, desligar e monitorar VMs remotamente
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import compute_v1
import os
import httpx
import logging
from typing import Dict, Any

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Açaí VM Controller",
    description="API para controlar VMs do GCP para processamento de açaí",
    version="1.0.0"
)

# Configurar CORS para permitir requisições do frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001", 
        "https://localhost:3000",
        "https://localhost:3001",
        "https://aplicativo-tcc-front.vercel.app",
        "https://aplicativo-tcc-front-r7ua652of-jefersons-projects-a8a24014.vercel.app",
        # Permitir outros deploys da Vercel
        #"*"  # Temporariamente para debug - você pode restringir depois
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Configurações do ambiente
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
ZONE = os.getenv("VM_ZONE", "us-central1-a")
VM_NAME = os.getenv("VM_NAME", "acai-detector-vm")

# Cliente do Compute Engine
compute_client = compute_v1.InstancesClient()

@app.get("/")
async def root():
    """Endpoint raiz com informações da API"""
    return {
        "message": "Açaí VM Controller API",
        "version": "1.0.0",
        "project_id": PROJECT_ID,
        "vm_name": VM_NAME,
        "zone": ZONE
    }

@app.get("/health")
async def health_check():
    """Health check para container"""
    return {"status": "healthy", "service": "acai-vm-controller"}

@app.get("/vm/status")
async def get_vm_status():
    """Retorna status atual da VM"""
    try:
        if not PROJECT_ID:
            raise HTTPException(status_code=500, detail="GCP_PROJECT_ID não configurado")
        
        instance = compute_client.get(
            project=PROJECT_ID,
            zone=ZONE,
            instance=VM_NAME
        )
        
        return {
            "status": instance.status,
            "name": instance.name,
            "zone": ZONE,
            "machine_type": instance.machine_type.split("/")[-1],
            "creation_timestamp": instance.creation_timestamp,
            "last_start_timestamp": instance.last_start_timestamp,
            "network_interfaces": [
                {
                    "network": ni.network.split("/")[-1],
                    "internal_ip": ni.network_i_p,
                    "external_ip": ni.access_configs[0].nat_i_p if ni.access_configs else None
                }
                for ni in instance.network_interfaces
            ]
        }
    except Exception as e:
        logger.error(f"Erro ao obter status da VM: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao obter status da VM: {str(e)}")

@app.post("/vm/start")
async def start_vm():
    """Liga a VM"""
    try:
        if not PROJECT_ID:
            raise HTTPException(status_code=500, detail="GCP_PROJECT_ID não configurado")
            
        # Verificar se já está rodando
        current_status = await get_vm_status()
        if current_status["status"] == "RUNNING":
            return {
                "message": "VM já está rodando",
                "status": "running"
            }
        
        operation = compute_client.start(
            project=PROJECT_ID,
            zone=ZONE,
            instance=VM_NAME
        )
        
        logger.info(f"VM {VM_NAME} iniciando... Operação: {operation.name}")
        
        return {
            "message": f"VM {VM_NAME} iniciando...",
            "operation_id": operation.name,
            "status": "starting"
        }
    except Exception as e:
        logger.error(f"Erro ao iniciar VM: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao iniciar VM: {str(e)}")

@app.post("/vm/stop")
async def stop_vm():
    """Desliga a VM"""
    try:
        if not PROJECT_ID:
            raise HTTPException(status_code=500, detail="GCP_PROJECT_ID não configurado")
            
        # Verificar se já está parada
        current_status = await get_vm_status()
        if current_status["status"] == "TERMINATED":
            return {
                "message": "VM já está parada",
                "status": "stopped"
            }
        
        operation = compute_client.stop(
            project=PROJECT_ID,
            zone=ZONE,
            instance=VM_NAME
        )
        
        logger.info(f"VM {VM_NAME} parando... Operação: {operation.name}")
        
        return {
            "message": f"VM {VM_NAME} parando...",
            "operation_id": operation.name,
            "status": "stopping"
        }
    except Exception as e:
        logger.error(f"Erro ao parar VM: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao parar VM: {str(e)}")

@app.get("/vm/operations/{operation_id}")
async def get_operation_status(operation_id: str):
    """Verifica status de uma operação da VM"""
    try:
        if not PROJECT_ID:
            raise HTTPException(status_code=500, detail="GCP_PROJECT_ID não configurado")
            
        operations_client = compute_v1.ZoneOperationsClient()
        operation = operations_client.get(
            project=PROJECT_ID,
            zone=ZONE,
            operation=operation_id
        )
        
        return {
            "operation_id": operation_id,
            "status": operation.status,
            "progress": operation.progress,
            "operation_type": operation.operation_type,
            "target_link": operation.target_link,
            "insert_time": operation.insert_time,
            "end_time": operation.end_time if hasattr(operation, 'end_time') else None
        }
    except Exception as e:
        logger.error(f"Erro ao verificar operação {operation_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao verificar operação: {str(e)}")

@app.api_route("/ml/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_to_ml_vm(path: str, request: Request):
    """
    Proxy requests para a VM de ML quando estiver rodando
    Permite usar a API da VM através deste controller
    """
    try:
        # Verificar se VM está rodando
        vm_status = await get_vm_status()
        if vm_status["status"] != "RUNNING":
            raise HTTPException(
                status_code=503, 
                detail=f"VM não está rodando (status: {vm_status['status']}). Ligue a VM primeiro."
            )
        
        # Obter IP externo da VM
        external_ip = None
        for ni in vm_status["network_interfaces"]:
            if ni["external_ip"]:
                external_ip = ni["external_ip"]
                break
        
        if not external_ip:
            raise HTTPException(
                status_code=503,
                detail="VM não possui IP externo configurado"
            )
        
        # Fazer proxy do request
        url = f"http://{external_ip}:5000/{path}"
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Preparar headers
            headers = dict(request.headers)
            if "host" in headers:
                del headers["host"]
            
            # Fazer request para a VM
            response = await client.request(
                method=request.method,
                url=url,
                content=await request.body(),
                headers=headers,
                params=request.query_params
            )
            
            return response.json()
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout ao conectar com a VM")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Não foi possível conectar com a VM")
    except Exception as e:
        logger.error(f"Erro no proxy para VM: {e}")
        raise HTTPException(status_code=500, detail=f"Erro no proxy: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080) 