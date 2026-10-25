// Azure AI Speech for voice narration (backend/src/matchlens/speech.py).
//
// Entra ID only: local (key) auth is disabled, the custom subdomain is what Entra tokens need,
// and the backend's managed identity gets "Cognitive Services Speech User". F0 includes
// 0.5 million neural characters a month, enough for the demo (one F0 Speech resource per
// subscription; use S0 if one already exists).
param location string
param tags object
param token string
@description('Principal id of the backend\'s managed identity.')
param principalId string
@allowed(['F0', 'S0'])
param sku string = 'F0'

var speechUserRole = 'f2dc8367-1007-4938-bd23-fe263f013447'

resource speech 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'speech-${token}'
  location: location
  tags: tags
  kind: 'SpeechServices'
  sku: { name: sku }
  properties: {
    customSubDomainName: 'speech-${token}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource speechUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(speech.id, principalId, speechUserRole)
  scope: speech
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', speechUserRole)
  }
}

output resourceId string = speech.id
output region string = speech.location
