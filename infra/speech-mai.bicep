// Standalone Azure AI Speech resource for the MAI-Voice text to speech models.
//
// Resource-group scope, deployed with `az deployment group create` (not part of `azd up`, which
// keeps the F0 narration resource in speech.bicep). Same conventions as speech.bicep: Entra ID
// only (local key auth disabled), a custom subdomain, and the "Cognitive Services Speech User"
// role for whoever calls it. Unlike speech.bicep this is S0: MAI voices are billed per character
// and F0 is not documented as supporting them.
//
// MAI voices are served from a limited set of regions, so `location` is restricted to the
// regions that Microsoft Learn marks for MAI voices in both the Speech regions table and the
// MAI-Voice page (checked 2026-10-27). `southindia` is not a standalone Speech region.
targetScope = 'resourceGroup'

@allowed([
  'canadacentral'
  'centralindia'
  'eastus'
  'eastus2'
  'francecentral'
  'southeastasia'
  'swedencentral'
  'westeurope'
  'westus2'
])
param location string = 'centralindia'

param tags object = {}

@description('Entra object id of a user or service principal that may call the resource (Cognitive Services Speech User). Leave empty to assign no role.')
param principalId string = ''

@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'

@description('Keep local (key) auth off so no secret exists. Set false only if a key is genuinely needed.')
param disableLocalAuth bool = true

var token = toLower(uniqueString(subscription().id, resourceGroup().id))
var speechUserRole = 'f2dc8367-1007-4938-bd23-fe263f013447'

resource speech 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'speech-mai-${token}'
  location: location
  tags: tags
  kind: 'SpeechServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'speech-mai-${token}'
    disableLocalAuth: disableLocalAuth
    publicNetworkAccess: 'Enabled'
  }
}

resource speechUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(speech.id, principalId, speechUserRole)
  scope: speech
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', speechUserRole)
  }
}

output name string = speech.name
output resourceId string = speech.id
output region string = speech.location
output endpoint string = speech.properties.endpoint
output ttsEndpoint string = 'https://${speech.location}.tts.speech.microsoft.com/cognitiveservices/v1'
