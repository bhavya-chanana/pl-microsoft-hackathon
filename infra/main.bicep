// Matchlens on Azure Container Apps (BRD §6.1, §10.5, §10.6).
//
//   browser ──▶ frontend (Next.js, public) ──▶ backend (FastAPI + agents, internal only)
//                                                  ├──▶ Cosmos DB serverless (managed identity)
//                                                  └──▶ Azure OpenAI in Foundry (managed identity)
//
// Both apps and the database scale to zero when idle. The backend plays one
// match per process, so it runs at most one replica; every run is stored in
// Cosmos DB and a cold start resumes a live run where it stopped. It replays
// the recorded demo match and only calls the model on a cache miss.
targetScope = 'subscription'

@minLength(1)
@maxLength(40)
@description('azd environment name; used to name the resource group and resources.')
param environmentName string

@minLength(1)
@description('Region for all resources.')
param location string

@description('Existing Azure OpenAI / AI Services account the agents call. Leave empty to run on the recorded demo and templates only.')
param openAiAccountName string = ''

@description('Resource group of that account.')
param openAiResourceGroup string = ''

@description('Azure OpenAI v1 endpoint. Defaults to the account\'s openai.azure.com endpoint.')
param openAiEndpoint string = ''

param modelFast string = 'gpt-5-mini'
param modelStrong string = 'gpt-5-mini'
param reasoningFast string = 'low'
param reasoningStrong string = 'high'

@description('Minimum backend replicas. 0 scales to zero; a cold start resumes the stored match where it stopped.')
@minValue(0)
@maxValue(1)
param backendMinReplicas int = 0

@description('Replay speed the demo match starts at.')
param matchSpeed string = '1'

@description('Speech tier for voice narration. F0 is free (one per subscription); use S0 if one exists.')
@allowed(['F0', 'S0'])
param speechSku string = 'F0'

@description('Set by azd after the first deploy, so re-provisioning keeps the running images.')
param backendImageName string = ''
param frontendImageName string = ''

var tags = { 'azd-env-name': environmentName }
var useOpenAi = !empty(openAiAccountName)
var endpoint = !empty(openAiEndpoint) ? openAiEndpoint : (useOpenAi ? 'https://${openAiAccountName}.openai.azure.com/openai/v1/' : '')

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module app 'app.bicep' = {
  name: 'matchlens-app'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    openAiEndpoint: endpoint
    modelFast: modelFast
    modelStrong: modelStrong
    reasoningFast: reasoningFast
    reasoningStrong: reasoningStrong
    backendMinReplicas: backendMinReplicas
    matchSpeed: matchSpeed
    backendImageName: backendImageName
    frontendImageName: frontendImageName
    speechSku: speechSku
  }
}

// The backend's identity may call the models; nothing else (no keys anywhere).
module openAiAccess 'openai-access.bicep' = if (useOpenAi) {
  name: 'matchlens-openai-access'
  scope: resourceGroup(useOpenAi ? openAiResourceGroup : rg.name)
  params: {
    accountName: openAiAccountName
    principalId: app.outputs.identityPrincipalId
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = app.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = app.outputs.registryName
output FRONTEND_URL string = app.outputs.frontendUrl
output BACKEND_INTERNAL_URL string = app.outputs.backendUrl
output MATCHLENS_COSMOS_ENDPOINT string = app.outputs.cosmosEndpoint
output MATCHLENS_SPEECH_REGION string = app.outputs.speechRegion
output MATCHLENS_SPEECH_RESOURCE_ID string = app.outputs.speechResourceId
output MATCHLENS_LLM_MODE string = useOpenAi ? 'online' : 'offline'
