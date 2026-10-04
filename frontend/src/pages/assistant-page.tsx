import { Sparkles } from 'lucide-react'

import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { VoiceAssistant } from '@/features/assistant/components'
import { NAV_GROUPS, getModule } from '@/features/modules/catalog'

/**
 * The assistant route.
 *
 * The header describes the one thing a reader most often gets wrong about this
 * surface — that NEXO routes a request to an existing service rather than
 * answering it. The model badge says it outright, because a page that opens with
 * a microphone is read as a chatbot and then judged by chatbot rules.
 */

const ASSISTANT = getModule('/assistant')

const GROUP_LABEL =
  NAV_GROUPS.find((group) => group.items.some((item) => item.to === ASSISTANT.to))?.label ??
  'Platform'

export default function AssistantPage() {
  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        eyebrow={GROUP_LABEL}
        title="AI Assistant"
        description={
          'Speak or type one request. NEXO classifies it into one of fourteen intents and ' +
          'names the service behind it. It routes your request — it does not write an answer.'
        }
        badges={
          <>
            <Badge variant="outline" className="gap-1 font-normal">
              <Sparkles aria-hidden="true" />
              Voice · Phase 12
            </Badge>
            <Badge variant="outline" className="font-normal">
              microsoft/deberta-v3-base
            </Badge>
          </>
        }
      />

      <VoiceAssistant />
    </div>
  )
}
