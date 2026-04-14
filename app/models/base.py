from pydantic import BaseModel, ConfigDict


class SeoLinkedModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
