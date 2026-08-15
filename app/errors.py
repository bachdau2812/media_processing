from pydantic import BaseModel, ConfigDict


class ApiProblem(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.title() for part in value.split("_")[1:]),
    )

    code: str
    message: str
    request_id: str
